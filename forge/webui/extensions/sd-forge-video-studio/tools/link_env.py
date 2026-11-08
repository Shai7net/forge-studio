"""Make the Video Studio engine environment as hard links of an existing venv (no extra disk space).

python link_env.py <source venv> <target venv>
Every file becomes a second name of the same data; deleting either venv later leaves the other whole.
pip and uv replace files instead of editing them in place, so an update of one never changes the other.
"""
import os
import shutil
import sys


def link_tree(src, dst):
    count = 0
    for root, dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        if "__pycache__" in rel.split(os.sep):
            continue
        out = os.path.join(dst, rel) if rel != "." else dst
        os.makedirs(out, exist_ok=True)
        for name in files:
            s, d = os.path.join(root, name), os.path.join(out, name)
            if os.path.exists(d):
                continue
            try:
                os.link(s, d)
            except OSError:
                shutil.copy2(s, d)
            count += 1
    return count


if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    n = link_tree(src, dst)
    print(f"linked {n} files into {dst}")
