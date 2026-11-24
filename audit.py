# Observed-limits probe: runs a script inside the exact bubblewrap sandbox catchcheck uses and reports what it can see.
import os, subprocess, tempfile, shutil, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import catchcheck as cc
tree = tempfile.mkdtemp(); out = tempfile.mkdtemp()
open(os.path.join(tree, "probe.py"), "w").write(r'''
import os, socket, subprocess, sys
def t(label, fn):
    try: print(label, "->", fn())
    except Exception as e: print(label, "-> BLOCKED/ERR", type(e).__name__, str(e)[:70])
t("uid/gid", lambda: (os.getuid(), os.getgid()))
t("env keys", lambda: sorted(os.environ))
t("ls /", lambda: sorted(os.listdir("/")))
t("ls /home", lambda: os.listdir("/home"))
t("read /etc/passwd", lambda: len(open("/etc/passwd").read()))
t("read a file under the invoking user's home", lambda: open(os.path.expanduser("~/.bashrc")).read()[:10] if os.path.exists(os.path.expanduser("~/.bashrc")) else "n/a")
t("write /usr", lambda: open("/usr/x","w"))
t("write /etc", lambda: open("/etc/x","w"))
t("write /w/tree (the exported copy)", lambda: open("/w/tree/x","w").write("1"))
t("ls /w", lambda: sorted(os.listdir("/w")))
t("write plugin dir", lambda: open("/w/plug/x","w"))
t("outbound TCP", lambda: socket.create_connection(("1.1.1.1", 80), timeout=3))
t("DNS", lambda: socket.gethostbyname("github.com"))
t("interfaces", lambda: sorted(os.listdir("/sys/class/net")) if os.path.exists("/sys/class/net") else "no /sys")
t("see host processes", lambda: sorted(p for p in os.listdir("/proc") if p.isdigit())[:10])
t("run git", lambda: subprocess.run(["git","--version"],capture_output=True,timeout=3).returncode)
t("fork bomb limit (RLIMIT_NPROC)", lambda: __import__("resource").getrlimit(__import__("resource").RLIMIT_NPROC))
t("mem limit (RLIMIT_AS)", lambda: __import__("resource").getrlimit(__import__("resource").RLIMIT_AS))
''')
cmd = cc.sandbox_cmd(tree, os.path.expanduser("~/venvs/base"), out, "python /w/tree/probe.py", 20, 512)
r = subprocess.run(cmd, capture_output=True, text=True)
print(r.stdout, r.stderr[:300])
