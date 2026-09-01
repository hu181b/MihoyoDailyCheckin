# MihoyoDailyCheckin · 米哈游国服每日签到

国服米游社每日签到，跑在 **GitHub Actions** 上每天定时执行，结果通过 **Server酱** 推送到微信。

- 🎮 **多游戏 + 独立开关**：**原神 / 星铁 / 绝区零** 各有一个 `enabled` 开关，按需开关；没玩的游戏自动跳过
- 👥 **多账号**：`COOKIE` 用 `#` 分隔即可
- 🔔 **微信通知**：标题随结果高亮（✅ 成功 / ⚠️ 验证码 / ❌ 失败）
- 📷 **扫码取 Cookie**：自带扫码登录工具，不用开发者工具手动抓；可直接写进 GitHub Secret，Cookie 不落屏
- 🔔 **失败不静默**：签到失败或通知推送失败时以非零码退出，Actions 变红并发失败邮件
- ☁️ **全自动**：部署后无需开机，云端每天自动跑

> ⚠️ **关于验证码**：国服签到接口会**随机触发 geetest 极验验证码**。多数情况能正常签到，偶尔被拦时通知里会显示「触发验证码」——这是国服自动签到的固有限制，不是 bug，隔天一般自动恢复，手动补签即可。
>
> ⚠️ **风险提示**：自动签到属灰色地带，理论上有风险（实际极罕见）。介意者请勿使用。Cookie 等于账号登录态，**只填进 GitHub Secrets，切勿发给他人或提交到公开代码**。

---

## 文件说明

| 文件 | 作用 |
|---|---|
| `sign.py` | 签到主逻辑（多游戏 / 多账号 / 通知） |
| `qr_login.py` | 扫码登录获取 Cookie 的工具 |
| `扫码取Cookie.cmd` | 双击即可运行 `qr_login.py`（免敲命令） |
| `get_cookie.py` | 从浏览器直接读取 Cookie（备用） |
| `.github/workflows/checkin.yml` | GitHub Actions 定时任务 |

---

## 一、获取米游社 Cookie

### 方法 A：扫码登录（推荐，不依赖浏览器）

双击 **`扫码取Cookie.cmd`**（会直接把 Cookie 写进 GitHub 的 `COOKIE` Secret，不显示在屏幕上），或手动运行：

```bash
pip install requests qrcode pillow
python qr_login.py --set-secret   # 直接写进 GitHub Secret（推荐，需已装并登录 gh）
python qr_login.py                # 或者打印出来，自己复制粘贴
```

弹出二维码图片 → 用 **米游社 App** 扫一扫（要快，二维码 1~2 分钟过期）→ 手机上确认登录 → 脚本取出整段 Cookie。

带 `--set-secret` 时值经 `gh` 的 stdin 写入 Secret，不打印、不进剪贴板、也不出现在进程命令行里；`gh` 不可用时会先问你，才降级为打印。Cookie 失效后重跑一次即可。

### 方法 B：从浏览器读取（备用）

前提：浏览器里登录了 mihoyo.com。

```bash
pip install browser_cookie3
python get_cookie.py
```

> Edge/Chrome 新版对 Cookie 做了 App-Bound 加密，常读取失败；读不到就用方法 A。

### 方法 C：开发者工具手动抓（兜底）

1. 电脑浏览器登录米游社，打开签到页 <https://act.mihoyo.com/bbs/event/signin/hk4e/index.html?bbs_auth_required=true&act_id=e202311201442471>。
2. 按 `F12` → `Network`（网络）面板，**勾选「禁用缓存」**，点 `Fetch/XHR`，刷新页面。
3. 点一个发往 `api-takumi.mihoyo.com` 的请求 → `Headers` → 复制请求头里的整段 `Cookie`。
   - 需包含 `cookie_token`/`account_id` 或 `ltoken`/`ltuid` 等字段。

> 多账号：把每个账号的 Cookie 用 `#` 连成一行，如 `cookieA#cookieB`。

## 二、获取 Server酱 SENDKEY（微信通知，可选）

1. 打开 <https://sct.ftqq.com/> 用微信扫码登录。
2. 复制 **SENDKEY**（形如 `SCTxxxxxxxxxxxx`）。
3. 在后台「消息通道」绑定微信，确保能收到推送。

> 不配 `SCT_KEY` 也能签到，只是没有微信通知，结果只在 Actions 日志里看。

## 三、部署到 GitHub Actions

1. **Fork** 本仓库到你自己账号（或新建仓库放入这些文件）。
2. 仓库 → `Settings` → `Secrets and variables` → `Actions` → `New repository secret`，添加：
   - `COOKIE`：上面拿到的米游社 Cookie（必填）。
   - `SCT_KEY`：Server酱 SENDKEY（可选）。
3. 仓库 → `Actions` → 选 `Mihoyo Daily Check-in` → `Run workflow` 手动跑一次验证。
4. 之后每天 **北京时间约 8:30** 自动执行（GitHub cron 有延迟，属正常）。

## 四、本地测试

```bash
pip install -r requirements.txt

# PowerShell
$env:COOKIE="你的Cookie"; $env:SCT_KEY="你的SENDKEY"; python sign.py

# bash
COOKIE="你的Cookie" SCT_KEY="你的SENDKEY" python sign.py
```

---

## 维护说明

国服接口、签名、活动 ID 会随官方更新而变化，相关常量都集中在 [sign.py](sign.py) 顶部：

- **`GAMES`**：每个游戏的开关与接口参数。
  - `enabled`：该游戏的总开关，`False` = 完全跳过、连接口都不请求。**当前只开了原神**，星铁和绝区零是关的；要签就把对应行改成 `True`。关掉的游戏会在日志和通知里明写"已关闭"，不做静默跳过。
  - `act_id`（签到活动 ID，见签到页 URL）和 `signgame`（luna 接口区分游戏的标识）。
  - 已实测：原神 `hk4e` / 星铁 `hkrpg` / 绝区零 `zzz`（注意绝区零的 biz 是 `nap_cn` 但 signgame 是 `zzz`，是个例外）。
  - 新增游戏或某游戏报 `retcode=-500001`，多半是 `act_id` 或 `signgame` 需更新。
- **`DS_SALT` / `APP_VERSION`**：DS 动态签名用，被风控时可能需要跟随官方更新。

扫码工具 [qr_login.py](qr_login.py) 顶部的 `SALT`（passport DS 盐）同理，失效时更新。
