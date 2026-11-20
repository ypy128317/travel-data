"""旅游数据平台 · 一键部署到云服务器（101.37.234.52）。

前置：已配置 SSH 免密（见 setup_sshkey.py，配置一次即可）。
用法：
    python deploy.py             # 全量：前端 + 后端（重建镜像）
    python deploy.py --frontend  # 只传前端（改 html/js 后用，秒级生效，不重启容器）
    python deploy.py --verify    # 只做公网验证

原理：前端 static 是 docker 挂载卷，覆盖文件后刷新浏览器即生效；
      后端 .py 打进镜像，改动后需 up -d --build 重建 backend 容器。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOST = "root@101.37.234.52"
REMOTE_DIR = "/opt/travel-data"
PUBLIC_URL = "http://101.37.234.52"

# 前端：本地相对路径 -> 服务器相对路径（static 为挂载卷）
FRONTEND_FILES = [
    ("app/static/index.html", "static/index.html"),
    ("app/static/app.js", "static/app.js"),
    ("app/static/map_template.html", "static/map_template.html"),
]
# 后端：打进镜像的 Python 源码（改动才需要重建）
BACKEND_FILES = [
    ("app/routers/ai.py", "app/routers/ai.py"),
    ("app/routers/guides.py", "app/routers/guides.py"),
    ("app/main.py", "app/main.py"),
    ("app/knowledge.py", "app/knowledge.py"),
    ("app/config.py", "app/config.py"),
    ("app/database.py", "app/database.py"),
]

SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new"]


def run(cmd: list[str], *, capture: bool = False, check: bool = True) -> subprocess.CompletedProcess:
    if capture:
        return subprocess.run(cmd, text=True, capture_output=True)
    return subprocess.run(cmd, check=check)


def ssh(remote_cmd: str, *, capture: bool = False, check: bool = True) -> subprocess.CompletedProcess:
    return run(["ssh", *SSH_OPTS, HOST, remote_cmd], capture=capture, check=check)


def step(msg: str) -> None:
    print("\n" + "=" * 60 + "\n" + msg + "\n" + "=" * 60, flush=True)


def check_ssh() -> bool:
    r = ssh("echo OK", capture=True)
    return r.returncode == 0 and "OK" in (r.stdout or "")


def upload(files: list[tuple[str, str]], label: str) -> int:
    n = 0
    for local_rel, remote_rel in files:
        local = ROOT / local_rel
        if not local.exists():
            print(f"  [跳过] 本地不存在: {local_rel}")
            continue
        remote = f"{REMOTE_DIR}/{remote_rel}"
        # 确保远端目录存在
        ssh(f"mkdir -p $(dirname {remote})", capture=True)
        r = run(["scp", *SSH_OPTS, str(local), f"{HOST}:{remote}"], capture=True)
        if r.returncode == 0:
            size = local.stat().st_size
            print(f"  [OK] {local_rel}  ->  {remote_rel}  ({size} bytes)")
            n += 1
        else:
            print(f"  [失败] {local_rel}: {(r.stderr or '').strip()}")
    print(f"{label}：成功上传 {n} 个文件")
    return n


def rebuild_backend() -> bool:
    step("4/5 重建并重启后端容器（这一步约 30-60 秒）")
    # 只重建 backend，不动 mysql，避免数据卷风险
    r = ssh(
        f"cd {REMOTE_DIR} && docker compose -f docker-compose.cloud.yml up -d --build backend",
        capture=True,
    )
    print((r.stdout or "").strip()[-1500:])
    if r.returncode != 0:
        print("构建输出(错误):", (r.stderr or "").strip()[-1500:])
        return False
    return True


def wait_healthy(timeout: int = 90) -> bool:
    step("5/5 等待容器健康 + 公网验证")
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = ssh(
            "docker ps --filter name=travel-backend --format '{{.Status}}'",
            capture=True,
        )
        status = (r.stdout or "").strip()
        if "Up" in status and "unhealthy" not in status:
            print(f"  容器状态: {status}")
            break
        print(f"  容器状态: {status or '启动中…'}")
        time.sleep(5)
    # 公网验证
    checks = [
        ("首页", "/"),
        ("前端JS", "/app.js"),
        ("地图页", "/map"),
        ("城市接口", "/api/meta/cities"),
    ]
    all_ok = True
    for name, path in checks:
        r = ssh(
            f"curl -s -o /dev/null -w '%{{http_code}}' --max-time 8 {PUBLIC_URL}{path}",
            capture=True,
        )
        code = (r.stdout or "").strip()
        ok = code == "200"
        all_ok = all_ok and ok
        print(f"  [{'OK' if ok else 'FAIL'}] {name:8s} {path:20s} HTTP {code}")
    return all_ok


def verify_only() -> bool:
    step("公网验证")
    r = ssh("docker ps --format '{{.Names}}  {{.Status}}'", capture=True)
    print((r.stdout or "").strip())
    checks = [("首页", "/"), ("前端JS", "/app.js"), ("地图页", "/map"), ("城市接口", "/api/meta/cities")]
    all_ok = True
    for name, path in checks:
        r = ssh(f"curl -s -o /dev/null -w '%{{http_code}}' --max-time 8 {PUBLIC_URL}{path}", capture=True)
        code = (r.stdout or "").strip()
        ok = code == "200"
        all_ok = all_ok and ok
        print(f"  [{'OK' if ok else 'FAIL'}] {name:8s} {path:20s} HTTP {code}")
    return all_ok


def main() -> int:
    ap = argparse.ArgumentParser(description="一键部署旅游平台到云服务器")
    ap.add_argument("--frontend", action="store_true", help="只部署前端（秒级，不重启后端）")
    ap.add_argument("--verify", action="store_true", help="只做公网验证")
    args = ap.parse_args()

    step("旅游数据平台 · 一键部署")
    print(f"服务器: {HOST}    公网: {PUBLIC_URL}")
    if not check_ssh():
        print("\n[错误] SSH 免密未配置或连不上服务器。")
        print("请先运行: python setup_sshkey.py <服务器密码>  （只需一次）")
        return 2
    print("SSH 免密连接正常 ✓")

    if args.verify:
        ok = verify_only()
        print("\n" + ("✓ 验证全部通过" if ok else "✗ 部分接口异常"))
        return 0 if ok else 1

    if args.frontend:
        step("1/2 上传前端（挂载卷，刷新即生效）")
        upload(FRONTEND_FILES, "前端")
        step("2/2 验证")
        ok = verify_only()
        print("\n" + ("✓ 前端已生效，验证通过" if ok else "✗ 验证异常"))
        return 0 if ok else 1

    # 全量部署
    step("1/5 上传前端（挂载卷，刷新即生效）")
    upload(FRONTEND_FILES, "前端")
    step("2/5 上传后端 Python 源码")
    upload(BACKEND_FILES, "后端")
    if not rebuild_backend():
        print("\n✗ 后端构建失败，请查看上面的错误输出")
        return 1
    ok = wait_healthy()
    print("\n" + ("=" * 60))
    print("✓ 部署完成！打开 " + PUBLIC_URL + " 查看" if ok else "✗ 部署完成但部分验证未通过，请检查")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
