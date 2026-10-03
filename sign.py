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
import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

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
REWARD_URL = "https://api-takumi.mihoyo.com/event/luna/home"


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


class NetworkError(SignError):
    pass


def request_json(method, url, **kwargs):
    """仅重试临时网络/服务故障；签到 POST 由角色层先核实状态再重试。"""
    attempts = 3 if method == "GET" else 1
    for attempt in range(attempts):
        try:
            response = requests.request(method, url, **kwargs)
            if response.status_code == 429 or response.status_code >= 500:
                raise NetworkError("网络或服务暂时异常")
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError("invalid response")
            return body
        except (requests.Timeout, requests.ConnectionError, ValueError, NetworkError):
            if attempt + 1 == attempts:
                raise NetworkError("网络超时或服务异常，请等待下一次自动补签") from None
            print(f"网络请求异常，稍后重试（{attempt + 1}/{attempts - 1}）")
            time.sleep(3 * (attempt + 1))
        except requests.RequestException:
            raise SignError("接口请求被拒绝，请检查服务状态") from None


def api_error(response):
    code = response.get("retcode")
    message = str(response.get("message") or "未知错误")
    if code in (10001, -100, -10001) or any(x in message.lower() for x in ("登录", "登陆", "cookie", "login", "login expired")):
        return "Cookie 已失效或登录状态无效：请重新获取 Cookie，并更新 GitHub Secrets 的 COOKIE"
    return f"接口返回异常：{message}（错误码 {code}）"


def get_roles(cookie: str, game: dict) -> list:
    """获取该 Cookie 下某游戏的角色列表（无角色返回空，不报错）。"""
    r = request_json("GET", 
        ROLE_URL, params={"game_biz": game["biz"]},
        headers=build_headers(cookie, game["signgame"]), timeout=20,
    )
    if r.get("retcode") != 0:
        raise SignError(api_error(r))
    return (r.get("data") or {}).get("list") or []


def reward_text(game: dict, headers: dict, total) -> str:
    """按本月累计签到次数查当天那一份奖励，不能按日历日期索引。"""
    unavailable = "今日签到奖励：暂未查询到（不影响签到结果）"
    try:
        if isinstance(total, bool) or not str(total).isdigit():
            return unavailable
        index = int(total) - 1
        if index < 0:
            return unavailable
        response = request_json("GET", 
            REWARD_URL,
            params={"lang": LANG, "act_id": game["act_id"]},
            headers=headers, timeout=15,
        )
        if response.get("retcode") != 0:
            return unavailable
        awards = (response.get("data") or {}).get("awards")
        if not isinstance(awards, list) or index >= len(awards):
            return unavailable
        award = awards[index]
        if not isinstance(award, dict):
            return unavailable
        name, count = award.get("name"), award.get("cnt")
        if not isinstance(name, str) or not name.strip() or isinstance(count, bool):
            return unavailable
        if not str(count).isdigit() or int(count) <= 0:
            return unavailable
        return f"今日签到奖励：{name.strip()} ×{int(count)}"
    except Exception:
        # 奖励查询失败不能中断签到或输出带敏感请求头的异常。
        return unavailable


def _sign_one_role(cookie: str, game: dict, role: dict) -> str:
    """对单个角色签到，返回结果文本。"""
    tag = f"[{game['name']}]"
    region = role["region"]
    uid = role["game_uid"]
    nickname = role.get("nickname", uid)
    act_id = game["act_id"]
    headers = build_headers(cookie, game["signgame"])

    # 已签天数信息
    info = request_json("GET", 
        INFO_URL, params={"lang": LANG, "act_id": act_id, "region": region, "uid": uid},
        headers=headers, timeout=20,
    )
    if info.get("retcode") != 0:
        return f"❌ {tag} {nickname}({uid}) {api_error(info)}"
    data = info.get("data") or {}
    if data.get("is_sign"):
        total = data.get("total_sign_day", "?")
        return (f"➖ {tag} {nickname}({uid}) 今天已签过，本月累计 {total} 天\n"
                f"{reward_text(game, headers, total)}")

    # 执行签到
    resp = request_json("POST",
        SIGN_URL,
        json={"act_id": act_id, "region": region, "uid": uid, "lang": LANG},
        headers=headers, timeout=20,
    )
    retcode = resp.get("retcode")
    rdata = resp.get("data") or {}

    if retcode == 0 and not rdata.get("risk_code") and not rdata.get("gt"):
        total = (data.get("total_sign_day") or 0) + 1
        return (f"✅ {tag} {nickname}({uid}) 签到成功，本月累计 {total} 天\n"
                f"{reward_text(game, headers, total)}")
    if retcode == -5003:
        # 另一处刚完成签到时，再查实际累计次数，避免把上一份奖励当作今日奖励。
        total = None
        try:
            latest = request_json("GET", 
                INFO_URL,
                params={"lang": LANG, "act_id": act_id, "region": region, "uid": uid},
                headers=headers, timeout=15,
            )
            latest_data = latest.get("data") or {}
            if latest.get("retcode") == 0 and latest_data.get("is_sign"):
                total = latest_data.get("total_sign_day")
        except Exception:
            pass
        days = f"，本月累计 {total} 天" if total is not None else ""
        return (f"➖ {tag} {nickname}({uid}) 今天已签过{days}\n"
                f"{reward_text(game, headers, total)}")
    if rdata.get("risk_code") or rdata.get("gt"):
        return f"⚠️ {tag} {nickname}({uid}) 触发验证码(geetest)，被拦截，需人工补签"
    return f"❌ {tag} {nickname}({uid}) 签到失败：{api_error(resp)}"


def sign_one_role(cookie: str, game: dict, role: dict) -> str:
    for attempt in range(3):
        try:
            return _sign_one_role(cookie, game, role)
        except NetworkError:
            if attempt < 2:
                print(f"[{game['name']}] 网络异常，重新查询签到状态后补试（{attempt + 1}/2）")
                time.sleep(5 * (attempt + 1))
                continue
            return f"❌ [{game['name']}] {role.get('nickname', '')} 网络异常：自动重试仍失败，等待下一次补签"
        except SignError as exc:
            return f"❌ [{game['name']}] {role.get('nickname', '')} {exc}"
        except Exception:
            return f"❌ [{game['name']}] 签到响应异常，请查看接口是否变化"


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
        except Exception:  # 不输出可能带 Cookie 的异常
            lines.append(f"❌ [{game['name']}] 角色查询响应异常")
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
        print(f"通知推送失败：{type(e).__name__}")
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


def today():
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")


def notify_once(title, content, status):
    """跨 Actions 运行去重；仅保存日期、状态和摘要，不保存账号或凭证。"""
    path = Path(os.environ.get("NOTIFY_STATE_PATH", ".checkin-state/notification.json"))
    date = today()
    destination = os.environ.get("TG_CHAT_ID", "") + os.environ.get("TG_BOT_TOKEN", "") + os.environ.get("SCT_KEY", "")
    scope = hashlib.sha256(destination.encode()).hexdigest()
    state = {}
    try:
        state = json.loads(path.read_text())
        if not isinstance(state, dict):
            state = {}
    except (OSError, ValueError):
        pass
    if state.get("date") != date or state.get("scope") != scope:
        state = {"date": date, "scope": scope, "success": False, "failures": []}
    digest = hashlib.sha256(content.encode()).hexdigest()
    if (status == "✅" and state.get("success")) or (status != "✅" and digest in state.get("failures", [])):
        print("今日同类通知已发送，跳过重复推送；签到检查仍正常执行")
        return True
    if not notify(title, content):
        return False
    # 未配置通知时不能登记为已发，以免后续首次配置被跳过。
    configured = bool(os.environ.get("TG_BOT_TOKEN") or os.environ.get("TG_CHAT_ID") or os.environ.get("SCT_KEY"))
    if configured:
        if status == "✅":
            state["success"] = True
        else:
            state.setdefault("failures", []).append(digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state))
        temporary.replace(path)
    return True


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

    body = f"签到日期：{today()}（北京时间）\n\n" + summary
    off = disabled_games()
    if off:
        body += "\n\n（已关闭：" + "/".join(g["name"] for g in off) + "）"
    pushed = notify_once(f"{status} 米哈游签到结果", body, status)

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

