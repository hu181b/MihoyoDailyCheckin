#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
米哈游 国服米游社 自动签到（原神 / 星铁 / 绝区零）
- 多账号：COOKIE 用 # 或换行分隔
- 多游戏：自动跳过没有角色的游戏
- 通知：Telegram Bot 优先，未配置时使用 Server酱
环境变量：
  COOKIE   必填，米游社 Cookie（多个用 # 分隔）
  SCT_KEY  选填，Server酱 Turbo 的 SENDKEY
  TG_BOT_TOKEN / TG_CHAT_ID  选填，Telegram Bot 凭证和接收聊天 ID
"""

import os
import time
import random
import string
import hashlib

import requests

# ============ 配置（接口/版本会变，失效时改这里）============
LANG = "zh-cn"
APP_VERSION = "2.71.1"       # 米游社 App 版本号
DS_SALT = "xV8v4Qu54lUKrEYFZkJhB8cuOh9Asafs"  # web 端 DS salt（如失效需更新）

# 各游戏配置：act_id 见各自签到页 URL，signgame 是 luna 接口区分游戏的标识
# 原神 act_id 已实测；星铁/绝区零为通行值，若你玩且报错把签到页 URL 里的 act_id 发我更新
#
# enabled: 该游戏的总开关。False = 完全跳过，连接口都不请求。
#   要开/关某个游戏，只改这一列，别处都不用动。
#   关掉的游戏会在日志和通知里明写"已关闭"，不做静默跳过 ——
#   否则哪天手滑关错了，只会看到"没签到"却找不到原因。
GAMES = [
    {"name": "原神",   "biz": "hk4e_cn",  "act_id": "e202311201442471", "signgame": "hk4e",  "enabled": True},
    {"name": "星铁",   "biz": "hkrpg_cn", "act_id": "e202304121516551", "signgame": "hkrpg", "enabled": False},
    {"name": "绝区零", "biz": "nap_cn",   "act_id": "e202406242138391", "signgame": "zzz",   "enabled": False},
]


def enabled_games() -> list:
    return [g for g in GAMES if g.get("enabled", True)]


def disabled_games() -> list:
    return [g for g in GAMES if not g.get("enabled", True)]

ROLE_URL = "https://api-takumi.mihoyo.com/binding/api/getUserGameRolesByCookie"
INFO_URL = "https://api-takumi.mihoyo.com/event/luna/info"
SIGN_URL = "https://api-takumi.mihoyo.com/event/luna/sign"


def get_ds() -> str:
    """计算 web 端 DS 动态签名 (t,r,md5)。"""
    t = str(int(time.time()))
    r = "".join(random.choices(string.ascii_letters + string.digits, k=6))
    c = hashlib.md5(f"salt={DS_SALT}&t={t}&r={r}".encode()).hexdigest()
    return f"{t},{r},{c}"


def build_headers(cookie: str, signgame: str) -> dict:
    return {
        "Cookie": cookie,
        "DS": get_ds(),
        "x-rpc-app_version": APP_VERSION,
        "x-rpc-client_type": "5",
        "x-rpc-platform": "4",
        "x-rpc-signgame": signgame,  # luna 接口靠它区分游戏，缺了/错了报 -500001
        "x-rpc-channel": "appstore",
        "x-rpc-device_id": "".join(random.choices(string.hexdigits.lower(), k=32)),
        "User-Agent": (f"Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
                       f"AppleWebKit/605.1.15 (KHTML, like Gecko) miHoYoBBS/{APP_VERSION}"),
        "Referer": "https://act.mihoyo.com",
        "Origin": "https://act.mihoyo.com",
        "Accept": "application/json, text/plain, */*",
    }


class SignError(Exception):
    pass


def get_roles(cookie: str, game: dict) -> list:
    """获取该 Cookie 下某游戏的角色列表（无角色返回空，不报错）。"""
    r = requests.get(
        ROLE_URL, params={"game_biz": game["biz"]},
        headers=build_headers(cookie, game["signgame"]), timeout=20,
    ).json()
    if r.get("retcode") != 0:
        raise SignError(f"获取角色失败: {r.get('message')} (retcode={r.get('retcode')})")
    return (r.get("data") or {}).get("list") or []


def sign_one_role(cookie: str, game: dict, role: dict) -> str:
    """对单个角色签到，返回结果文本。"""
    tag = f"[{game['name']}]"
    region = role["region"]
    uid = role["game_uid"]
    nickname = role.get("nickname", uid)
    act_id = game["act_id"]
    headers = build_headers(cookie, game["signgame"])

    # 已签天数信息
    info = requests.get(
        INFO_URL, params={"lang": LANG, "act_id": act_id, "region": region, "uid": uid},
        headers=headers, timeout=20,
    ).json()
    if info.get("retcode") != 0:
        return (f"❌ {tag} {nickname}({uid}) 查询失败: {info.get('message')} "
                f"(retcode={info.get('retcode')})")
    data = info.get("data") or {}
    if data.get("is_sign"):
        total = data.get("total_sign_day", "?")
        return f"➖ {tag} {nickname}({uid}) 今天已签过，本月累计 {total} 天"

    # 执行签到
    resp = requests.post(
        SIGN_URL,
        json={"act_id": act_id, "region": region, "uid": uid, "lang": LANG},
        headers=headers, timeout=20,
    ).json()
    retcode = resp.get("retcode")
    rdata = resp.get("data") or {}

    if retcode == 0 and not rdata.get("risk_code"):
        total = (data.get("total_sign_day") or 0) + 1
        return f"✅ {tag} {nickname}({uid}) 签到成功，本月累计 {total} 天"
    if retcode == -5003:
        return f"➖ {tag} {nickname}({uid}) 今天已签过"
    if rdata.get("risk_code") or rdata.get("gt"):
        return f"⚠️ {tag} {nickname}({uid}) 触发验证码(geetest)，被拦截，需人工补签"
    return f"❌ {tag} {nickname}({uid}) 签到失败: {resp.get('message')} (retcode={retcode})"


def run_account(cookie: str, idx: int) -> str:
    head = f"【账号{idx}】"
    lines = []
    games = enabled_games()
    for game in games:
        try:
            roles = get_roles(cookie, game)
        except SignError as e:
            lines.append(f"❌ [{game['name']}] {e}")
            continue
        except Exception as e:  # noqa: BLE001
            lines.append(f"❌ [{game['name']}] 异常: {e}")
            continue
        if not roles:
            continue  # 没这个游戏的角色，静默跳过
        for role in roles:
            lines.append(sign_one_role(cookie, game, role))
            time.sleep(random.uniform(1, 3))
    if not lines:
        if not games:
            lines.append("⚠️ GAMES 里没有任何启用的游戏，请检查 enabled 配置")
        else:
            names = "/".join(g["name"] for g in games)
            lines.append(f"⚠️ 在已启用的游戏({names})里未找到角色，"
                         f"请检查 Cookie 是否有效/为国服账号")
    return head + "\n" + "\n".join(lines)


def status_emoji(text: str) -> str:
    """根据结果给通知标题选高亮图标。"""
    if "❌" in text:
        return "❌"
    if "⚠️" in text:
        return "⚠️"
    return "✅"


def notify_serverchan(title: str, content: str) -> bool:
    """推送 Server酱 通知。返回是否确实推送成功。

    注意 requests 不抛异常 ≠ 推送成功：SCT_KEY 失效/额度用尽时
    Server酱 照样返回 HTTP 200，只是 body 里 code != 0。
    只看异常会在"通知根本没发出去"时打印"已推送"，把故障藏起来。
    """
    key = os.environ.get("SCT_KEY", "").strip()
    if not key:
        return True  # 有意不配通知，不算失败
    try:
        resp = requests.post(
            f"https://sctapi.ftqq.com/{key}.send",
            data={"title": title, "desp": content},
            timeout=20,
        )
        body = resp.json()
        if body.get("code") != 0:
            print(f"通知推送被拒: {body.get('message')} (code={body.get('code')})")
            return False
        print("已推送 Server酱 通知")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"通知推送失败: {e}")
        return False


def notify_telegram(title: str, content: str) -> bool:
    """发送纯文本通知；凭证只从环境变量读取，不输出含 Token 的异常。"""
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TG_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("Telegram 配置不完整：需要 TG_BOT_TOKEN 和 TG_CHAT_ID")
        return False
    text = f"{title}\n\n{content}"
    # 2000 个 Unicode 字符，即使包含 emoji 也不会超过消息长度限制。
    for offset in range(0, len(text), 2000):
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text[offset:offset + 2000]},
                timeout=20,
            )
            body = resp.json()
            if resp.status_code != 200 or body.get("ok") is not True:
                print(f"Telegram 通知失败（HTTP {resp.status_code}，"
                      f"错误码 {body.get('error_code', '?')}）。"
                      "请检查 Token、聊天 ID，并先给机器人发送 /start。")
                return False
        except Exception as exc:
            # requests 异常可能包含带 Token 的 URL，不能打印异常正文。
            print(f"Telegram 通知请求失败：{type(exc).__name__}")
            return False
    print("Telegram 通知发送成功")
    return True


def notify(title: str, content: str) -> bool:
    """已配置 Telegram 时优先使用；否则使用原有 Server酱通知。"""
    if os.environ.get("TG_BOT_TOKEN", "").strip() or os.environ.get("TG_CHAT_ID", "").strip():
        return notify_telegram(title, content)
    return notify_serverchan(title, content)


def main():
    raw = os.environ.get("COOKIE", "").strip()
    if not raw:
        print("未设置 COOKIE 环境变量")
        raise SystemExit(1)

    cookies = [c.strip() for c in raw.replace("\n", "#").split("#") if c.strip()]
    on_names = "/".join(g["name"] for g in enabled_games()) or "(无)"
    off_names = "/".join(g["name"] for g in disabled_games())
    print(f"启用: {on_names}" + (f"   已关闭: {off_names}" if off_names else ""))

    results = []
    for i, ck in enumerate(cookies, 1):
        res = run_account(ck, i)
        print(res)
        results.append(res)
        time.sleep(random.uniform(2, 5))  # 账号间随机间隔，降低风控

    summary = "\n\n".join(results)
    # 先定 status 再拼附注，保证附注文本永远不会污染成败判定
    status = status_emoji(summary)

    body = summary
    off = disabled_games()
    if off:
        body += "\n\n（已关闭：" + "/".join(g["name"] for g in off) + "）"
    pushed = notify(f"{status} 米哈游签到结果", body)

    # 失败时必须让 Actions 变红，换来一封 GitHub 失败邮件。
    # 否则 Server酱 一旦失效，"其实一个都没签上"会被绿勾完全掩盖 —— 2026-09-01
    # 就是这样：工作流被停用一周，唯一的感知渠道只剩通知，而通知也没了。
    # 判定复用 status_emoji，和通知标题共用一套标准，避免两处漂移：
    #   ✅ 全部成功或今天已签过 / ➖ 已签过 → 正常
    #   ❌ 签到失败 / ⚠️ 验证码拦截、没找到角色 → 都要人工介入
    if status != "✅":
        raise SystemExit(1)
    if not pushed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
