#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
扫码登录 → 自动获取米游社 Cookie（不依赖浏览器）

用法：
  pip install requests qrcode pillow

  python qr_login.py                # 打印 Cookie，自己复制粘贴
  python qr_login.py --set-secret   # 扫完直接写进 GitHub 的 COOKIE Secret（推荐）

流程：运行后弹出二维码 → 用【米游社 App】扫一扫 → 手机上确认登录
      → 脚本取出 Cookie → 打印，或直接写进 Secret。

--set-secret 走 gh CLI 的 stdin 传值，Cookie 不显示在屏幕上、
不进剪贴板、也不会出现在进程命令行里（--body 会，所以没用它）。

⚠️ Cookie = 账号钥匙，只填进 GitHub Secrets，别发任何人。
"""

import time
import json
import uuid
import random
import string
import hashlib
import os
import sys
import shutil
import argparse
import subprocess

import requests

DEFAULT_REPO = "PTAbabybearR/MihoyoDailyCheckin"
ARGS = None  # argparse 结果，output() 要用

SALT = "JwYDpKvLj6MrMqqYU6jTKF17KNO2PXoS"  # passport web DS 盐（失效时更新）
APP_ID = "bll8iq97cem8"                     # 米游社 bbs
PASSPORT = "https://passport-api.mihoyo.com/account/ma-cn-passport/web"
DEVICE = uuid.uuid4().hex

S = requests.Session()  # 全程用同一会话，登录态会落进 S.cookies


def ds(body: str = "", query: str = "") -> str:
    t = str(int(time.time()))
    r = "".join(random.choices(string.ascii_letters + string.digits, k=6))
    c = hashlib.md5(f"salt={SALT}&t={t}&r={r}&b={body}&q={query}".encode()).hexdigest()
    return f"{t},{r},{c}"


def headers(body: str = "") -> dict:
    return {
        "x-rpc-app_id": APP_ID,
        "x-rpc-device_id": DEVICE,
        "x-rpc-client_type": "2",
        "DS": ds(body=body),
        "Content-Type": "application/json",
        "User-Agent": "okhttp/4.9.3",
    }


def create_qr():
    body = "{}"
    r = S.post(f"{PASSPORT}/createQRLogin", data=body,
               headers=headers(body), timeout=20).json()
    if r.get("retcode") != 0:
        raise SystemExit(f"取二维码失败: {r.get('message')} ({r.get('retcode')})")
    return r["data"]["url"], r["data"]["ticket"]


def show_qr(url: str):
    import qrcode
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    saved = False
    try:
        img = qr.make_image()
        path = os.path.abspath("login_qr.png")
        img.save(path)
        print(f"二维码已生成: {path}")
        if sys.platform == "win32":
            os.startfile(path)
            print("（已自动打开图片，用米游社 App 扫它，扫快点！）")
        saved = True
    except Exception as e:  # noqa: BLE001
        print(f"图片生成失败: {e}")
    try:
        qr.print_ascii(invert=True)
    except Exception:
        if not saved:
            print("终端无法显示二维码，且图片生成失败，请检查 pillow 是否安装")


def poll(ticket: str) -> dict:
    print("\n请用【米游社 App】扫描二维码，并在手机上确认登录...")
    body = json.dumps({"ticket": ticket}, separators=(",", ":"))
    last = None
    for _ in range(150):  # ~5 分钟
        r = S.post(f"{PASSPORT}/queryQRLoginStatus", data=body,
                   headers=headers(body), timeout=20).json()
        if r.get("retcode") != 0:
            raise SystemExit(f"二维码已失效: {r.get('message')} ({r.get('retcode')})，请重跑")
        data = r["data"]
        st = data.get("status")
        if st != last:
            print({"Created": "  · 等待扫描...",
                   "Scanned": "  · 已扫描，请在手机上点【确认登录】",
                   "Confirmed": "  · 已确认 ✅"}.get(st, f"  · {st}"))
            last = st
        if st == "Confirmed":
            return data
        time.sleep(2)
    raise SystemExit("超时未确认，请重跑")


# ---- 从 stoken 兜底换取 cookie_token / ltoken ----
def get_cookie_token(stoken, aid, mid):
    ck = f"stuid={aid};stoken={stoken};mid={mid}"
    r = S.get("https://api-takumi.mihoyo.com/auth/api/getCookieAccountInfoBySToken",
              params={"stoken": stoken, "uid": aid},
              headers={"Cookie": ck, "x-rpc-app_id": APP_ID}, timeout=20).json()
    if r.get("retcode") == 0 and r.get("data"):
        return r["data"].get("cookie_token")
    return None


def get_ltoken(stoken, aid, mid):
    ck = f"stuid={aid};stoken={stoken};mid={mid}"
    r = S.get("https://passport-api.mihoyo.com/account/auth/api/getLTokenBySToken",
              headers={"Cookie": ck, "x-rpc-app_id": APP_ID, "DS": ds()}, timeout=20).json()
    if r.get("retcode") == 0 and r.get("data"):
        return r["data"].get("ltoken")
    return None


def set_secret(cookie_str: str, repo: str) -> bool:
    """把 Cookie 写进仓库的 COOKIE Secret。

    值走 stdin 而不是 --body，这样它不会出现在进程命令行里
    （命令行同机器上别的进程可见，也可能落进 shell 历史）。
    """
    exe = shutil.which("gh")
    if not exe:
        print("✗ 找不到 gh 命令：GitHub CLI 没装或不在 PATH")
        return False

    r = subprocess.run([exe, "auth", "status"],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        print("✗ gh 未登录，先跑一次：gh auth login")
        return False

    r = subprocess.run([exe, "secret", "set", "COOKIE", "--repo", repo],
                       input=cookie_str.encode("utf-8"),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0:
        err = (r.stderr or b"").decode("utf-8", "replace").strip()
        print(f"✗ 写入 Secret 失败: {err}")
        return False

    print(f"\n✅ 已写入 {repo} 的 COOKIE Secret，值全程没有显示在屏幕上。")
    print("   下一步：去 Actions 手动触发一次，或等下一个定时点。")
    return True


def output(cookie_str: str):
    if ARGS is not None and ARGS.set_secret:
        if set_secret(cookie_str, ARGS.repo):
            return
        # 自动写入失败时不能默默把 Cookie 甩到屏幕上 —— 用户选的是"别落屏"，
        # 得他自己点头才降级。
        print("\n自动写入没成功。")
        try:
            ans = input("要改为把 Cookie 打印到屏幕、你自己复制吗？(y/N) ").strip().lower()
        except EOFError:
            ans = "n"
        if ans != "y":
            print("已放弃打印。修好 gh 后重跑本脚本即可。")
            sys.exit(1)

    print("\n========== 复制下面这一整行 → 粘贴到 GitHub 的 COOKIE Secret ==========\n")
    print(cookie_str)
    print("\n========== 复制到上面这行结束 ==========")
    print("\n⚠️ 这串 = 账号钥匙，只填进 GitHub Secrets，别发给任何人。")


def main():
    global ARGS
    ap = argparse.ArgumentParser(description="扫码登录获取米游社 Cookie")
    ap.add_argument("--set-secret", action="store_true",
                    help="扫码后直接写进 GitHub 的 COOKIE Secret，不打印到屏幕")
    ap.add_argument("--repo", default=DEFAULT_REPO,
                    help=f"目标仓库，默认 {DEFAULT_REPO}")
    ARGS = ap.parse_args()

    url, ticket = create_qr()
    show_qr(url)
    data = poll(ticket)

    ui = data.get("user_info") or {}
    aid, mid = ui.get("aid"), ui.get("mid")

    # 方案一：登录态可能已写进会话 cookie（Set-Cookie）
    jar = {c.name: c.value for c in S.cookies}
    print(f"\n[诊断] 会话 cookie: {list(jar.keys())}")

    if any(k in jar for k in ("cookie_token", "cookie_token_v2", "ltoken_v2", "stoken_v2", "stoken")):
        # 补全 account_id / ltuid 方便后续接口
        jar.setdefault("account_id", aid)
        jar.setdefault("ltuid", aid)
        if mid:
            jar.setdefault("mid", mid)
        output(";".join(f"{k}={v}" for k, v in jar.items()))
        return

    # 方案二：会话里有 stoken 就换 cookie_token
    stoken = jar.get("stoken") or jar.get("stoken_v2")
    if stoken:
        ct = get_cookie_token(stoken, aid, mid)
        lt = get_ltoken(stoken, aid, mid)
        parts = [f"account_id={aid}", f"ltuid={aid}", f"stuid={aid}",
                 f"mid={mid}", f"stoken={stoken}"]
        if ct:
            parts.insert(1, f"cookie_token={ct}")
        if lt:
            parts.append(f"ltoken={lt}")
        if ct or lt:
            output(";".join(parts))
            return

    # 都没有 → 打印诊断
    print("\n⚠️ 没能取到登录态，诊断信息（发给协助者，不含敏感值）：")
    print("  会话 cookie 名:", list(jar.keys()))
    print("  user_info keys:", list(ui.keys()))
    print("  data keys:", list(data.keys()))
    print("  ext:", data.get("ext"))
    print("  token_types:", [t.get("token_type") for t in (data.get("tokens") or [])])
    sys.exit(1)


if __name__ == "__main__":
    main()
