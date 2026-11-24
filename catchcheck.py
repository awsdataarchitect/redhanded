#!/usr/bin/env python3
"""catchcheck: evidence for "does this PR's new/changed test react to the PR's code change?"

Runs the PR's new and modified Python tests (pytest) on two trees:
  HEAD = the PR's code,  BASE = the code before the PR + the PR's test files overlaid.
Classifies each test by HOW it behaves (assertion failure vs import error vs runtime
error vs passes-both vs flaky) and links it to the changed source lines through
per-test coverage. Output is diagnostic evidence for a reviewer, never proof and
never a fraud label.

Isolation: every pytest run happens inside bubblewrap: no network, read-only system,
only an exported tree + a wheels-only venv mounted, cleared environment, wall-clock
timeout, memory/cpu/file ulimits. Trees come from `git archive` exports, so the git
dir is never the cwd of untrusted code.

Labels describe behavior on these two trees only. ASSERTION-DIFFERS means an assertion fails on base and passes on head;
it does not show that the test covers the bug a PR claims to fix.

Design note: the red/green idea (run new tests on old code) is the same one failfirst
uses (https://github.com/BenMalaga/failfirst, MIT). No failfirst source is used here.
"""
import argparse, ast, json, os, re, shutil, subprocess, tempfile, xml.etree.ElementTree as ET

TEST_FILE_RE = re.compile(r"(^|/)(test_[^/]*\.py|[^/]*_test\.py|conftest\.py)$")
TEST_DIR_RE = re.compile(r"(^|/)(tests?|testing)/")


def git(args, cwd, check=True):
    r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError("git %s: %s" % (args, r.stderr.strip()))
    return r.stdout


def is_test_surface(p):
    return p.endswith(".py") and bool(TEST_FILE_RE.search(p) or TEST_DIR_RE.search(p))


def is_runnable_test(p):
    return bool(re.search(r"(^|/)(test_[^/]*\.py|[^/]*_test\.py)$", p))


def export_tree(gitdir, rev, dest):
    os.makedirs(dest, exist_ok=True)
    p1 = subprocess.Popen(["git", "archive", rev], cwd=gitdir, stdout=subprocess.PIPE)
    subprocess.run(["tar", "-x", "--no-same-owner", "--no-same-permissions", "-C", dest],
                   stdin=p1.stdout, check=True)
    p1.wait()
    if p1.returncode:
        raise RuntimeError("git archive failed")


def show(gitdir, rev, path):
    r = subprocess.run(["git", "show", "%s:%s" % (rev, path)], cwd=gitdir, capture_output=True)
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None


def test_funcs(src):
    out = {}
    if src is None:
        return out
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out

    def walk(body, prefix):
        for n in body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test"):
                out[prefix + n.name] = ast.dump(n)
            elif isinstance(n, ast.ClassDef):
                walk(n.body, prefix + n.name + "::")
    walk(tree.body, "")
    return out


def func_ranges(src):
    out = {}
    try:
        tree = ast.parse(src or "")
    except SyntaxError:
        return out

    def walk(body, prefix):
        for n in body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test"):
                out[prefix + n.name] = (n.lineno - len(n.decorator_list), n.end_lineno)
            elif isinstance(n, ast.ClassDef):
                walk(n.body, prefix + n.name + "::")
    walk(tree.body, "")
    return out


def added_lines(gitdir, base, head, path):
    diff = git(["diff", "--unified=0", base, head, "--", path], gitdir, check=False)
    lines, cur = set(), 0
    for raw in diff.splitlines():
        m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", raw)
        if m:
            cur = int(m.group(1)) - 1
        elif raw.startswith("+") and not raw.startswith("+++"):
            cur += 1
            lines.add(cur)
    return lines


PLUG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugdir")


def sandbox_cmd(tree, venv, outdir, inner, timeout, mem_mb):
    ul = "ulimit -v %d; ulimit -t %d; ulimit -f 2000000; " % (mem_mb * 1024, timeout)
    env = {"PATH": "/venv/bin:/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8",
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": "/w/tree:/w/tree/src", "CI": "1"}
    args = ["timeout", "-k", "5", str(timeout + 20), "bwrap", "--unshare-all", "--die-with-parent", "--clearenv",
            "--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib",
            "--symlink", "usr/lib64", "/lib64", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
            "--ro-bind", venv, "/venv", "--bind", tree, "/w/tree", "--bind", outdir, "/w/out",
            "--ro-bind", PLUG, "/w/plug", "--chdir", "/w/tree"]
    for k, v in env.items():
        args += ["--setenv", k, v]
    return args + ["/bin/sh", "-c", ul + inner]


def top_names(src):
    out = set()
    try:
        tree = ast.parse(src or "")
    except SyntaxError:
        return out
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    out.add(t.id)
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            out.add(n.target.id)
    return out


def modname_for(path):
    p = re.sub(r"^src/", "", path)[:-3]
    p = re.sub(r"/__init__$", "", p)
    return p.replace("/", ".")


def run_pytest(tree, venv, outdir, files, tag, cov=False, timeout=300, mem_mb=1536, lenient=False):
    cmd = ("python -m pytest -p no:cacheprovider -p no:randomly -o addopts= -q --continue-on-collection-errors "
           "--junitxml=/w/out/%s.xml -o junit_family=xunit2 " % tag)
    if cov:
        cmd += ("--cov --cov-context=test --cov-config=/w/out/coveragerc "
                "--cov-report=json:/w/out/%s.cov.json " % tag)
    if lenient:
        cmd = cmd.replace("python -m pytest", "PYTHONPATH=/w/tree:/w/tree/src:/w/plug python -m pytest -p catchcheck_lenient", 1)
    cmd += " ".join("'%s'" % f for f in files) + " > /w/out/%s.log 2>&1" % tag
    return subprocess.run(sandbox_cmd(tree, venv, outdir, cmd, timeout, mem_mb),
                          capture_output=True, text=True).returncode


def parse_junit(path):
    if not os.path.exists(path):
        return None
    res = {}
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return None
    for tc in root.iter("testcase"):
        cls, name = tc.get("classname", ""), tc.get("name", "")
        f, e, s = tc.find("failure"), tc.find("error"), tc.find("skipped")
        node = f if f is not None else e
        r = {"status": "pass", "kind": "", "msg": "", "exc": None}
        if node is not None:
            text = (node.get("message") or "") + "\n" + (node.text or "")
            r["status"] = "fail"
            elines = [l[1:].strip() for l in text.splitlines() if l.startswith("E ")]
            m = re.search(r"^E\s+([A-Za-z_.]+(?:Error|Exception|NotFound\w*|Exit))(?::|\s*$)", text, re.M)
            r["exc"] = m.group(1) if m else None
            r["msg"] = (elines[0] if elines else (node.get("message") or "")[:200])[:240]
            if e is not None and "collection failure" in (e.get("message") or ""):
                r["kind"] = "collection"
            elif (r["exc"] or "").endswith("AssertionError") or re.search(r"^E\s+assert |DID NOT RAISE|pytest\.fail", text, re.M) \
                    or (node.get("message") or "").startswith("assert"):
                r["kind"] = "assertion"
            else:
                r["kind"] = "error"
        elif s is not None:
            r["status"], r["msg"] = "skip", (s.get("message") or "")[:120]
        res[(cls, name)] = r
    return res


def key_for(cls, name, files):
    base_name = re.sub(r"\[.*\]$", "", name)
    parts = cls.split(".") if cls else []
    for i in range(len(parts), 0, -1):
        cand = "/".join(parts[:i]) + ".py"
        if cand in files:
            return cand, "::".join(parts[i:] + [base_name])
    cand = name.replace(".", "/") + ".py"
    if cand in files:
        return cand, "*"
    return None, None


def aggregate(runs, files):
    per = {}
    for run in runs:
        if run is None:
            continue
        seen = {}
        for (cls, name), r in run.items():
            f, q = key_for(cls, name, files)
            if f is not None:
                seen.setdefault((f, q), []).append(r)
        for k, lst in seen.items():
            fails = [x for x in lst if x["status"] == "fail"]
            per.setdefault(k, []).append(fails[0] if fails else lst[0])
    return per


def params_info(runs, f, q):
    """per-parameter outcomes on the first head/base run: {'head': (n,fail), 'base': (n,fail)}"""
    out = {}
    for side in ("head", "base"):
        r0 = runs[side][0] if runs[side] else None
        if not r0:
            continue
        n = nf = 0
        for (cls, name), r in r0.items():
            ff, qq = key_for(cls, name, {f})
            if ff == f and qq == q:
                n += 1
                nf += r["status"] == "fail"
        out[side] = [n, nf]
    return out


def summarize(lst):
    st = {x["status"] for x in lst}
    stable = len(st) == 1
    first = lst[0]
    return {"stable": stable, "status": first["status"] if stable else "mixed",
            "kind": first["kind"], "msg": first["msg"], "exc": first["exc"]}


MISSING_SYMBOL = {"ImportError", "ModuleNotFoundError", "AttributeError", "NameError"}


def classify(base, head, covers, imp=0):
    if head is None:
        return "NOT-RUN-ON-HEAD", "test did not run on head tree"
    if not head["stable"] or (base and not base["stable"]):
        return "FLAKY", "outcome differs between reruns on the same tree"
    if head["status"] == "skip":
        return "SKIPPED", head["msg"]
    if head["status"] == "fail":
        if base and base["status"] == "fail" and base["msg"] == head["msg"]:
            return "FAILS-ON-BOTH", "same failure on base and head (likely sandbox/environment or pre-existing): %s" % head["msg"][:120]
        return "FAILS-ON-HEAD", "%s: %s" % (head["kind"], head["msg"])
    if base is None or base["status"] == "skip":
        return "BASE-NOT-RUN", "test absent or skipped on base run"
    cv = "" if covers is None else ", executes %d changed line(s)" % covers
    if imp and not covers:
        cv = "; %d changed line(s) run only at import time (module-level data), so per-test linkage is unavailable" % imp
        covers = None if covers == 0 else covers
    if base["status"] == "pass":
        if covers is None:
            return "PASSES-BOTH", "coverage linkage unavailable" + ("" if not imp else " (changed lines run at import time only)")
        if covers > 0:
            return "PASSES-BOTH-EXERCISES-CHANGE", "runs %d changed line(s) but assertion cannot tell old from new behavior" % covers
        return "PASSES-BOTH-UNRELATED", "runs no changed source line; consistent with a characterization test of unchanged behavior"
    if (base["exc"] or "").endswith("MissingSymbolError"):
        return "NEEDS-NEW-API", "this test uses a symbol that does not exist on base: shows the API is new, not that old behavior was wrong%s" % cv
    if base["kind"] == "collection":
        if base["exc"] in MISSING_SYMBOL and covers == 0 and not imp:
            return "NEEDS-NEW-API-NO-CHANGED-LINES", "file cannot import on base (%s) but on head this test executes no changed source line: it may only import the new symbol while checking old behavior" % base["exc"]
        if base["exc"] in MISSING_SYMBOL:
            return "NEEDS-NEW-API", "file cannot import on base (%s: %s): shows a symbol is new, not that behavior was reproduced%s" % (base["exc"], base["msg"][:100], cv)
        return "BASE-COLLECT-ERROR", "collection failed on base: %s" % base["msg"][:140]
    if base["kind"] == "assertion":
        if covers == 0:
            return "ASSERTION-DIFFERS-NO-CHANGED-LINES", "assertion fails on base, passes on head, no changed source line executed (non-code cause?): %s" % base["msg"][:140]
        return "ASSERTION-DIFFERS", "assertion fails on base, passes on head%s: %s" % (cv, base["msg"][:140])
    exc = (base["exc"] or "error").split(".")[-1]
    msg = re.sub(r"^%s:\s*" % re.escape(exc), "", base["msg"])[:100]
    if exc == "TypeError" and re.search(r"argument|positional|keyword", base["msg"]):
        hint = "looks like a signature change (new parameter?), not necessarily the bug"
    else:
        hint = "raises on base where head succeeds; plausible behavior difference, review the traceback"
    return "BASE-RUNTIME-ERROR", "%s on base (%s)%s; %s" % (exc, msg, cv, hint)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gitdir", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--venv", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--label", default="")
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--instrument-missing-names", action="store_true",
                    help="OFF by default. For test files that cannot import on the untouched base, run a second, "
                         "separately labelled pass where names that exist only on head are stubbed. Heuristic: the "
                         "result is a counterfactual, never the base itself.")
    a = ap.parse_args()

    gd, out = os.path.abspath(a.gitdir), os.path.abspath(a.out)
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    base_sha = git(["rev-parse", a.base], gd).strip()
    head_sha = git(["rev-parse", a.head], gd).strip()
    entries = []
    for line in git(["diff", "--name-status", "-M", base_sha, head_sha], gd).splitlines():
        p = line.split("\t")
        entries.append((p[0][0], p[-1], p[1] if p[0][0] in "RC" else None))
    changed_tests = [e for e in entries if e[0] != "D" and is_test_surface(e[1])]
    run_files = [e[1] for e in changed_tests if is_runnable_test(e[1])]
    if a.only:
        run_files = [f for f in run_files if f in a.only]
    src_changed = [e[1] for e in entries if e[0] != "D" and e[1].endswith(".py") and not is_test_surface(e[1])]
    nonpy = [e[1] for e in entries if not e[1].endswith(".py")]

    meta = {}
    for st, path, old in changed_tests:
        if path not in run_files:
            continue
        oldf = test_funcs(None if st in "AC" or old and st == "C" else show(gd, base_sha, old or path))
        for q, d in test_funcs(show(gd, head_sha, path)).items():
            meta[(path, q)] = "new" if q not in oldf else ("modified" if oldf[q] != d else "unchanged")
    # test files whose changed lines fall outside new/modified test functions (shared data, helpers,
    # fixtures, parametrization lists): we cannot attribute the change to a test, so judge the whole file
    whole = set()
    for st, path, old in changed_tests:
        if path not in run_files:
            continue
        rng = func_ranges(show(gd, head_sha, path))
        tl = added_lines(gd, base_sha, head_sha, path)
        inside = set()
        for q, (a0, a1) in rng.items():
            if meta.get((path, q)) in ("new", "modified"):
                inside |= set(range(a0, a1 + 1))
        if st in "AC" and not old:
            continue  # brand-new file: every test in it is new
        hl = (show(gd, head_sha, path) or "").splitlines()
        trivial = lambda n: not hl[n - 1].strip() or re.match(r"\s*(#|import |from \S+ import )", hl[n - 1])
        if any(not trivial(n) for n in tl - inside if 0 < n <= len(hl)):
            whole.add(path)
    clm = {sp: added_lines(gd, base_sha, head_sha, sp) for sp in src_changed}

    work = tempfile.mkdtemp(prefix="cc-")
    try:
        ht, bt = os.path.join(work, "head"), os.path.join(work, "base")
        export_tree(gd, head_sha, ht)
        export_tree(gd, base_sha, bt)
        for st, path, old in changed_tests:
            dst = os.path.join(bt, path)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(os.path.join(ht, path), dst)
        with open(os.path.join(out, "coveragerc"), "w") as f:
            inc = "\n".join("    /w/tree/" + sp for sp in src_changed)
            f.write("[run]\ninclude =\n%s\n[json]\nshow_contexts = True\n" % inc)
        files = set(run_files)
        lenient = {}
        for sp in src_changed:
            new_names = top_names(show(gd, head_sha, sp)) - top_names(show(gd, base_sha, sp))
            if new_names:
                lenient[modname_for(sp)] = sorted(new_names)
        json.dump(lenient, open(os.path.join(out, "lenient.json"), "w"))
        selectors = sorted({"%s::%s" % (f, q) for (f, q), k in meta.items() if k != "unchanged" and f not in whole})
        selectors += sorted(whole)
        for f in run_files:  # files with no collectable test (or that fail to parse) run whole
            if not any(sel.startswith(f + "::") for sel in selectors):
                selectors.append(f)
        runs, rcs = {"head": [], "base": []}, {"head": [], "base": []}
        venv = os.path.abspath(a.venv)
        for side, tree in (("head", ht), ("base", bt)):
            for i in range(a.k):
                tag = "%s%d" % (side, i)
                rcs[side].append(run_pytest(tree, venv, out, selectors, tag, cov=(side == "head" and i == 0 and not whole), lenient=False, timeout=a.timeout))
                runs[side].append(parse_junit(os.path.join(out, tag + ".xml")))
        # second, separately labelled pass: only files that failed to COLLECT on untouched base
        Bstrict_pre = aggregate(runs["base"], files)
        bad_files = sorted({f for (f, q), lst in Bstrict_pre.items() if q == "*" and any(x["status"] == "fail" for x in lst)})
        inst_runs = []
        if bad_files and lenient and a.instrument_missing_names:
            sel2 = list(bad_files)  # whole files: a nodeid with no collector aborts the run
            for i in range(a.k):
                tag = "baseinstr%d" % i
                run_pytest(bt, venv, out, sel2, tag, cov=False, lenient=True, timeout=a.timeout)
                inst_runs.append(parse_junit(os.path.join(out, tag + ".xml")))
        BI = aggregate(inst_runs, files)
        env_fail = {s: all(r is None for r in runs[s]) for s in runs}
        H, B = aggregate(runs["head"], files), aggregate(runs["base"], files)

        cov_by_test = None
        covp = os.path.join(out, "head0.cov.json")
        if os.path.exists(covp) and clm:
            cov_by_test = {}
            import_time = set()
            for fpath, info in json.load(open(covp)).get("files", {}).items():
                rel = fpath.replace("/w/tree/", "", 1)
                if rel not in clm:
                    continue
                for ln, ctxs in (info.get("contexts") or {}).items():
                    for c in ctxs:
                        node = re.sub(r"\[.*?\](\|.*)?$", "", c.split("|")[0])
                        if node == "":
                            import_time.add((rel, int(ln)))
                        elif "::" in node:
                            cov_by_test.setdefault(node, set()).add((rel, int(ln)))
        rows = []
        for (f, q), kind in sorted(meta.items()):
            h = summarize(H[(f, q)]) if (f, q) in H else (summarize(H[(f, "*")]) if (f, "*") in H else None)
            b = summarize(B[(f, q)]) if (f, q) in B else (summarize(B[(f, "*")]) if (f, "*") in B else None)
            basis = "untouched-base"
            strict_b = b
            if (f, q) not in B and (f, q) in BI:
                b = summarize(BI[(f, q)])
                basis = "instrumented-base (counterfactual; missing names stubbed)"
            covers, imp = None, 0
            if cov_by_test is not None:
                hits = {x for x in cov_by_test.get("%s::%s" % (f, q), set()) if x[1] in clm[x[0]]}
                covers = len(hits)
                def _data_line(x):  # def/class headers of new code run at import for every test: noise
                    try:
                        t = open(os.path.join(ht, x[0]), encoding="utf-8", errors="replace").read().splitlines()[x[1] - 1]
                    except Exception:
                        return True
                    return not re.match(r"\s*(def |class |@|async def )", t)
                imp = len({x for x in import_time if x[1] in clm[x[0]] and _data_line(x)})
            v, why = classify(b, h, covers, imp)
            if basis != "untouched-base":
                src_txt = show(gd, head_sha, f) or ""
                probes = sorted(set(re.findall(r"\b(hasattr|getattr|dir|vars|inspect|importlib|importorskip|skipif|skipTest|__dict__|try)\b", src_txt)))
                if probes:
                    # stubbed names can change control flow in files that probe or guard on names: do not report a verdict
                    v = "INCONCLUSIVE-INSTRUMENTED"
                    why = "base only runs with stubbed names, and this file uses %s, which the stub could steer; no verdict (%s)" % (", ".join(probes), why[:80])
                else:
                    v = v + " [instrumented]"
                if not probes:
                    why = "BASE RESULT IS FROM AN INSTRUMENTED RUN, not the untouched base (untouched base: %s). %s" % (
                        (strict_b["exc"] or "collection error") if strict_b else "n/a", why)
            rows.append({"file": f, "test": q, "kind": kind, "verdict": v, "why": why, "basis": basis,
                         "params": params_info(runs, f, q),
                         "base": b and b["status"] + ("/" + b["kind"] if b["kind"] else ""), "head": h and h["status"]})
        if whole:
            extra = []
            for k in sorted(set(H) | set(B)):
                f, q = k
                if f not in whole or q == "*" or (f, q) in meta and meta[(f, q)] in ("new", "modified"):
                    continue
                h = summarize(H[k]) if k in H else None
                b = summarize(B[k]) if k in B else (summarize(B[(f, "*")]) if (f, "*") in B else None)
                v, why = classify(b, h, None)
                if v in ("PASSES-BOTH", "SKIPPED", "BASE-NOT-RUN"):
                    continue
                extra.append({"file": f, "test": q, "kind": "unattributed-change", "verdict": v, "why": why,
                              "base": b and b["status"] + ("/" + b["kind"] if b["kind"] else ""), "head": h and h["status"]})
            if (len(extra) > 25):
                print_note = "%d tests differ; showing 25" % len(extra)
                extra = extra[:25]
            rows += extra
        rep = {"whole_file_mode": sorted(whole), "label": a.label, "base": base_sha, "head": head_sha, "k": a.k, "changed_source_files": src_changed,
               "changed_non_python": nonpy[:20], "test_files_run": run_files, "pytest_exit_codes": rcs,
               "env_fail": env_fail, "coverage_linkage": cov_by_test is not None, "rows": rows}
        json.dump(rep, open(os.path.join(out, "report.json"), "w"), indent=1)
        show_report(rep)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def show_report(r):
    print("catchcheck %s\n  base %s\n  head %s\n  reruns/tree %d  coverage-linkage %s  env_fail %s  exit %s" %
          (r["label"], r["base"][:10], r["head"][:10], r["k"], r["coverage_linkage"], r["env_fail"], r["pytest_exit_codes"]))
    print("  changed source: %s" % (", ".join(r["changed_source_files"]) or "-"))
    if r["changed_non_python"]:
        print("  non-python changes (not coverage-linked): %s" % ", ".join(r["changed_non_python"][:6]))
    for x in r["rows"]:
        if x["kind"] != "unchanged":
            pi = x.get("params", {})
            ps = ""
            if pi.get("base") and pi["base"][0] > 1:
                ps = "  params failing on base: %d/%d" % (pi["base"][1], pi["base"][0])
            print("  [%s] (%s) %s::%s  base=%s head=%s%s\n      %s" % (x["verdict"], x["kind"], x["file"], x["test"], x["base"], x["head"], ps, x["why"]))
    print("  (%d unchanged pre-existing tests not judged)" % sum(1 for x in r["rows"] if x["kind"] == "unchanged"))


if __name__ == "__main__":
    main()
