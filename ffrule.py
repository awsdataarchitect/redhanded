# Applies failfirst v0.1.0's verdict rules (src/verdict.js, re-implemented in 6 lines, no code copied) to
# catchcheck's measured base/head results, to show where the two approaches would differ on the same PRs.
# This is NOT a run of failfirst (it cannot execute Python).
import json,glob,os
tot=0;diff=0;lines=[]
for f in sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "results", "*.report.json"))):
    r=json.load(open(f))
    if r["label"].startswith("synthetic"): continue
    for x in r["rows"]:
        if x["kind"] in ("unchanged",): continue
        b=(x["base"] or "absent"); h=x["head"]
        if x["kind"]=="modified": ff="PRE-EXISTING (not gated)"
        elif x["kind"]=="unattributed-change": ff="PRE-EXISTING (not gated)"
        elif h=="skip": ff="SKIPPED"
        elif b.startswith("pass"): ff="VACUOUS (gate fails)"
        elif h=="pass": ff="GOOD"
        else: ff="BROKEN"
        tot+=1
        same = (ff=="GOOD" and x["verdict"] in("ASSERTION-DIFFERS","ASSERTION-DIFFERS-NO-CHANGED-LINES","NEEDS-NEW-API","BASE-RUNTIME-ERROR"))
        if not same: diff+=1
        lines.append("%-22s %-48s %-28s | failfirst-rule: %-26s %s"%(r["label"],x["test"][-48:],x["verdict"],ff,"" if same else "<-- differs"))
print("\n".join(lines)); print("\n%d judged tests, %d where the two rules give a different answer"%(tot,diff))
