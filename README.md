# B站主播开播监控

监控"永夜秋殇"在 Bilibili 的开播状态。主播从"未开播"变为"正在直播"时，通过 Server酱 Turbo 给微信发一次通知；同一场直播不重复通知；下播后再次开播会重新通知。

云端由 GitHub Actions 每 10 分钟自动运行一次，**电脑关机也照常工作，长期 0 成本**。

## 工作原理

```
GitHub Actions 每 10 分钟启动（UTC，全天）
  ↓
checkout 仓库 → 读取 state.json（上次是否在播）
  ↓
python main.py 查询 B站直播间接口（无需 Cookie）
  ↓
未开播→开播？是 → Server酱发微信通知 + 更新 state.json
未开播→开播？否 → 只更新 state.json（下播）或不动（状态不变）
  ↓
git diff 检测 state.json 变化才 commit 回仓库
  ↓
本次运行结束，等下一个 10 分钟
```

## 项目结构

```
bilibili-live-notify/
├── main.py                          # 主程序（单文件，含 B站查询/状态/通知）
├── state.json                       # 跨运行状态（自动生成，需提交回仓库）
├── requirements.txt                # 只依赖 requests
├── .gitignore
├── .github/workflows/check-live.yml # GitHub Actions 定时任务
└── README.md
```

## 本地测试

```powershell
cd d:\python_test\bilibili-live-notify

# 1. 正常检查（查 B站真实状态，首次运行会初始化 state.json，不发通知）
python main.py

# 2. 测试通知链路（需先设 SendKey，否则会报"未检测到 SERVERCHAN_SENDKEY"）
$env:SERVERCHAN_SENDKEY="你的SCT开头的key"; python main.py --test-notification
```

## 部署到 GitHub（完整步骤）

### 1. 申请 Server酱 SendKey

1. 打开 https://sct.ftqq.com ，微信扫码登录
2. 进入 https://sct.ftqq.com/sendkey
3. 复形 `SCT` 开头的 SendKey（**不要给任何人，不要写进代码**）

### 2. 创建 GitHub Public 仓库

1. 登录 GitHub → 点右上角 `+` → New repository
2. Repository name 填 `bilibili-live-notify`
3. 选 **Public**（免费 Actions 额度对公开仓库最高）
4. **不**勾选 "Add a README"、"Add .gitignore"、license（避免冲突）
5. Create repository

### 3. 上传项目文件

把以下文件全部上传到仓库根目录（保留目录结构）：

**必须上传：**
- `main.py`
- `requirements.txt`
- `.gitignore`
- `.github/workflows/check-live.yml`（保留 .github/workflows/ 子目录层级）

**自动生成（不用手动创建，首次运行后自动产生并提交）：**
- `state.json`

**绝对不能上传：**
- 任何含 `SERVERCHAN_SENDKEY` 真实值的文件（SendKey 只放 GitHub Secrets）

上传方式任选：用 GitHub 网页上传、或 `git push`、或 GitHub Desktop。

### 4. 配置 GitHub Secret

1. 仓库页面 → Settings → Secrets and variables → Actions
2. New repository secret
3. Name 填 `SERVERCHAN_SENDKEY`
4. Secret 填第 1 步复制的 SendKey
5. Add secret

### 5. 首次手动运行测试

1. 仓库页面 → Actions 标签
2. 左侧选 "B站开播监控"
3. 点 "Run workflow" → Run workflow
4. 点进本次 run，看日志：
   - 应显示 `首次运行，初始化状态` + `主播：永夜秋殇 状态：未开播` + `本次不发送通知`
   - workflow 最后应成功 commit 一条 `chore: update live state`
   - 仓库根目录出现 `state.json`

### 6. 测试通知链路

1. Actions → "B站开播监控" → Run workflow
2. 这次改为运行测试通知：在 "Run workflow" 弹窗无法传参，所以用下面任一方式：
   - 临时改 workflow 在 `python main.py` 后加 `--test-notification` 跑一次，或
   - 本地先 `$env:SERVERCHAN_SENDKEY="..."; python main.py --test-notification` 验证微信能收到
3. 微信应收到标题"B站开播监控测试"的推送

### 7. 确认自动运行

部署后无需操作，GitHub 每 10 分钟自动跑一次。确认方法：
- Actions 标签下能看到每隔约 10 分钟一条 run 记录
- 永夜秋殇开播时，微信会收到 `【B站开播】永夜秋殇开播啦` 通知

### 8. 日常维护

- **更新代码**：改完 `git push`，下次 cron 自动用新代码
- **临时停用**：Actions → 选中 workflow → 右上角 `···` → Disable workflow
- **重新启用**：同样位置 Enable workflow
- **改监控主播**：改 `main.py` 顶部 `ROOM_ID` 和 `ANCHOR_NAME` 两行
- **看日志**：Actions → 点对应 run → 点 check job

## 成本说明（长期 0 元）

| 项目 | 费用 |
|------|------|
| GitHub Public 仓库 + Actions | 免费（公开仓库每月 2000 分钟，本项目每次 <30 秒，远低于上限） |
| GitHub-hosted ubuntu runner | 免费，无需信用卡 |
| Server酱 Turbo 免费方案 | 5 条/天，开播通知低频，足够 |
| 程序运行 | 不调用任何 AI、不调用付费 API |

## 常见问题

**Q: 第一次部署时主播正好在播，会误发通知吗？**
不会。首次运行（无 state.json）只记录当前状态、不发通知。代价是这次开播会错过通知，需等"下播→再开播"才通知。

**Q: B站接口临时故障会把主播误判成下播吗？**
不会。查询失败时不修改 state.json，本轮跳过，等下一个 10 分钟。

**Q: 会不会每 10 分钟产生一个 commit 把提交记录刷爆？**
不会。只在状态变化时才更新 state.json 并提交；状态不变时 git diff 为空，不 commit。

**Q: 为什么 SendKey 不会泄露？**
它只存在 GitHub Secrets，运行时注入环境变量，Python 用 `os.getenv` 读取，日志绝不打印完整 key。
