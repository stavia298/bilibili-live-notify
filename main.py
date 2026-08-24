# -*- coding: utf-8 -*-
"""
B站主播开播监控（GitHub Actions 单次运行版）

功能：检查"永夜秋殇"是否开播，仅在"未开播 → 开播"时通过 Server酱 发一次微信通知。

运行模式：GitHub Actions 每 10 分钟调用一次，每次只检查一次后退出（不做 while 循环）。
  GitHub Actions 启动 → Python 检查一次 → 判断状态 → 必要时通知 → 保存状态 → 结束

状态持久化（跨 GitHub Actions 运行保留）：
  上次是否在直播保存在仓库的 state.json（由 workflow 用 git commit 提交回仓库）。
  - 首次运行（无 state.json）：记录当前状态作为初始状态，不通知（避免部署时主播恰好在播被误通知）
  - 未开播 → 开播：发通知 + 更新状态
  - 开播 → 未开播：更新状态（下播不发通知）
  - 状态不变：不动 state.json
  - B站请求失败：不修改状态（避免误判下播），等下一次 cron

用法：
  python main.py                        # 正常检查（GitHub Actions 调用）
  python main.py --test-notification    # 只发一条测试通知，不查 B站、不改状态
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

# ============ 配置区 ============
ROOM_ID = 25779849            # 永夜秋殇的房间号（沿用 8.24.py 已验证的值）
ANCHOR_NAME = "永夜秋殇"       # 硬编码主播名，省掉一次主播信息接口请求
# ================================

# B站直播间信息接口（公开，无需 Cookie）
ROOM_INFO_URL = "https://api.live.bilibili.com/room/v1/Room/get_info"
# Server酱 Turbo 推送接口
SERVERCHAN_URL = "https://sctapi.ftqq.com/{key}.send"
# 状态文件：与 main.py 同目录（GitHub Actions checkout 后即仓库根目录）
STATE_FILE = Path(__file__).parent / "state.json"

# 北京时区（GitHub Actions runner 默认 UTC，日志/通知时间转成北京时间更直观）
CST = timezone(timedelta(hours=8))

# 伪装浏览器 UA（B站拒绝非浏览器请求）
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# 日志配置：带北京时间戳，方便在 GitHub Actions 日志里看时间
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
# 把日志时间戳强制设为北京时间（runner 默认 UTC）
logging.Formatter.converter = lambda *a: datetime.now(CST).timetuple()
logger = logging.getLogger("live-notify")


def now_str():
    """当前北京时间字符串"""
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")


def get_room_info(room_id, timeout=10.0):
    """
    查询直播间信息（保留自原 8.24.py，增加稳定性处理）。

    返回 dict：live_status / title / room_id / live_time
    异常：HTTP 错误、JSON 解析失败、B站业务码错误均抛出，由调用方处理，
          保证单次失败不会让程序崩溃或误判状态。
    """
    resp = requests.get(ROOM_INFO_URL, params={"room_id": room_id},
                        headers=HEADERS, timeout=timeout)
    # HTTP 状态码异常（4xx/5xx）
    resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError as e:
        # B站偶尔返回非 JSON 的错误页
        raise RuntimeError(f"解析 B站返回 JSON 失败: {e}")
    if data.get("code") != 0:
        # B站业务层错误（如房间号不存在）
        raise RuntimeError(f"B站接口错误: {data.get('message', '未知')}")
    info = data["data"]
    return {
        "live_status": info.get("live_status", 0),
        "title": info.get("title", "未知标题"),
        "room_id": info.get("room_id", room_id),
        # live_time: 开播时间字符串（开播时才有，格式如 "2024-08-24 20:00:00"）
        "live_time": info.get("live_time", "") or "",
    }


def is_living(live_status):
    """live_status == 1 才算真正在直播（2 是轮播，按未直播处理）"""
    return live_status == 1


def send_notification(title, desp):
    """
    通过 Server酱 Turbo 发送微信通知（独立模块，换渠道只改这里）。

    SendKey 从环境变量 SERVERCHAN_SENDKEY 读取（GitHub Actions Secret 注入），
    绝不硬编码、绝不打印完整 key。
    返回 True=成功，False=失败（调用方据此决定是否更新状态）。
    """
    key = os.getenv("SERVERCHAN_SENDKEY")
    if not key:
        logger.error("未检测到 SERVERCHAN_SENDKEY，请在 GitHub Actions Secrets 中配置。")
        return False
    try:
        resp = requests.post(
            SERVERCHAN_URL.format(key=key),
            data={"title": title, "desp": desp},
            headers=HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") != 0:
            logger.error("Server酱返回错误: %s", result.get("message", "未知"))
            return False
        return True
    except Exception as e:
        logger.error("Server酱发送异常: %s", e)
        return False


def load_state():
    """
    读取上次状态。返回 None 表示首次运行（state.json 不存在）。
    结构：{"was_living": bool, "last_live_time": str|null, "last_check_time": str}
    """
    if not STATE_FILE.exists():
        return None
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("读取 state.json 失败: %s，按首次运行处理", e)
        return None


def save_state(was_living, last_live_time):
    """写入状态文件（仅在状态变化时调用，避免每 10 分钟产生无意义 commit）"""
    state = {
        "was_living": was_living,
        "last_live_time": last_live_time,
        "last_check_time": now_str(),
    }
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def run_check():
    """
    单次检查流程（GitHub Actions 每次调用跑一遍后退出）。

    返回 exit code：0=正常，1=需注意（配置/通知失败，让 workflow 显示失败便于发现）。
    """
    prev = load_state()
    try:
        info = get_room_info(ROOM_ID)
    except Exception as e:
        # B站请求失败：不修改状态，避免误判下播，等下一次 cron
        logger.error("Bilibili 请求失败：%s", e)
        logger.info("本轮不修改状态，等待下一次定时检查")
        return 0

    now_living = is_living(info["live_status"])
    title = info["title"]
    live_time = info["live_time"]

    # 首次运行：记录当前状态作为初始状态，不通知
    # 影响：若部署时主播恰好在播，本次不会发通知（错过这次），需等下次"下播→开播"才通知
    if prev is None:
        logger.info("首次运行，初始化状态")
        logger.info("主播：%s 状态：%s", ANCHOR_NAME, "直播中" if now_living else "未开播")
        logger.info("本次不发送通知")
        save_state(now_living, live_time if now_living else None)
        return 0

    was_living = bool(prev.get("was_living", False))

    if now_living and not was_living:
        # 未开播 → 开播：发送通知
        logger.info("检测到状态变化：未开播 → 直播")
        logger.info("标题：%s", title)
        notify_title = f"【B站开播】{ANCHOR_NAME}开播啦"
        # 开播时间：优先用 B站返回的 live_time，没有则用检测时间
        open_time = live_time or now_str()
        notify_desp = (
            f"**{ANCHOR_NAME}开播啦！**\n\n"
            f"直播标题：{title}\n"
            f"开播时间：{open_time}\n"
            f"直播间：https://live.bilibili.com/{ROOM_ID}\n"
        )
        if send_notification(notify_title, notify_desp):
            logger.info("Server酱通知发送成功")
            save_state(True, live_time)
            logger.info("状态保存成功")
            return 0
        else:
            # 通知失败（无 key / Server酱错误）：不更新状态，下次 cron 重试
            return 1
    elif not now_living and was_living:
        # 开播 → 未开播：更新状态，不发通知
        logger.info("检测到状态变化：直播 → 未开播")
        logger.info("状态已更新")
        save_state(False, None)
        return 0
    else:
        # 状态不变：不动 state.json，workflow 里 git diff 不会产生 commit
        if now_living:
            logger.info("主播：%s 状态：直播中，此前已经通知，本次不重复推送", ANCHOR_NAME)
        else:
            logger.info("主播：%s 状态：未开播，本次无需通知", ANCHOR_NAME)
        return 0


def run_test_notification():
    """发送一条测试通知，不查 B站、不改状态，用于验证 Server酱 链路是否打通"""
    logger.info("发送测试通知（不修改直播状态）")
    ok = send_notification(
        "B站开播监控测试",
        "B站开播监控测试成功\n\n收到这条消息说明：GitHub Actions → Python → Server酱 → 微信，链路已通。",
    )
    return 0 if ok else 1


def main():
    parser = argparse.ArgumentParser(description="B站主播开播监控")
    parser.add_argument(
        "--test-notification", action="store_true",
        help="只发一条测试通知，不查 B站、不改状态",
    )
    args = parser.parse_args()

    if args.test_notification:
        sys.exit(run_test_notification())
    sys.exit(run_check())


if __name__ == "__main__":
    main()
