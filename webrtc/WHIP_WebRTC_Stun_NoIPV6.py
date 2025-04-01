import asyncio
import random
import logging
import cv2
import os
import time
from aiohttp import ClientSession
from aiortc import RTCPeerConnection, VideoStreamTrack, RTCConfiguration, RTCSessionDescription, RTCIceServer
from av import VideoFrame
from aiortc.contrib.media import MediaBlackhole

# 禁用IPv6以避免潜在问题
os.environ['AIORTC_IPv6'] = '0'

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("WHIP_Publisher")


class CameraStreamTrack(VideoStreamTrack):
    """自定义视频流轨道，直接从摄像头获取帧"""

    def __init__(self, camera_index=0, width=640, height=480, fps=30):
        super().__init__()  # 初始化父类
        self.camera = cv2.VideoCapture(camera_index)
        if not self.camera.isOpened():
            raise RuntimeError(f"摄像头 {camera_index} 打开失败")

        # 设置摄像头参数
        self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.camera.set(cv2.CAP_PROP_FPS, fps)

        # 控制帧率
        self.fps = fps
        self.frame_interval = 1.0 / fps
        self.last_frame_time = time.time()

        logger.info("摄像头已就绪 (%dx%d @%dfps)", width, height, fps)

    async def recv(self):
        # 控制帧率
        now = time.time()
        elapsed = now - self.last_frame_time
        if elapsed < self.frame_interval:
            await asyncio.sleep(self.frame_interval - elapsed)
        self.last_frame_time = time.time()

        # 读取帧
        ret, frame = self.camera.read()
        if not ret:
            raise RuntimeError("无法从摄像头读取帧")

        # 转换为YUV420P格式（VP8/VP9推荐格式）
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2YUV_I420)

        # 创建视频帧（使用整数时间戳）
        av_frame = VideoFrame.from_ndarray(frame, format="yuv420p")
        av_frame.pts = int(time.time() * 1000)  # 毫秒级时间戳
        av_frame.time_base = "1/1000"

        return av_frame

    def __del__(self):
        if self.camera.isOpened():
            self.camera.release()


async def whip_publish_webrtc():
    pc = None
    try:
        # 配置ICE服务器（STUN+TURN）
        pc = RTCPeerConnection(
            configuration=RTCConfiguration(
                iceServers=[

                    # TURN服务器（需要认证）
                    RTCIceServer(
                        # urls= "stun:stun.l.google.com:19302"
                        urls="stun:159.75.120.92:3478",
                        username="myuser",
                        credential="mypassword"
                    )
                ]
            )
        )

        # 添加ICE状态监控
        @pc.on("iceconnectionstatechange")
        async def on_ice_change():
            state = pc.iceConnectionState
            logger.info(f"ICE状态变化: {state}")
            if state == "failed":
                logger.error("ICE连接失败!")

        # 添加候选收集监控
        @pc.on("icecandidate")
        def on_ice_candidate(candidate):
            if candidate:
                logger.debug(f"发现候选: {candidate.candidate}")

        # 添加视频轨道
        video_track = CameraStreamTrack()
        pc.addTrack(video_track)

        # 创建并设置offer
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)

        # 发送WHIP请求
        whip_url = "http://huai-xhy.site:7777/whip/123456789"
        async with ClientSession() as session:
            async with session.post(
                    whip_url,
                    data=pc.localDescription.sdp,
                    headers={"Content-Type": "application/sdp"}
            ) as response:
                if response.status != 201:
                    error = await response.text()
                    logger.error(f"WHIP请求失败: {response.status} - {error}")
                    raise RuntimeError(f"WHIP请求失败: {response.status}")

                # 处理响应
                location = response.headers.get('Location', '')
                logger.info(f"WHIP Location: {location}")

                answer_sdp = await response.text()
                answer = RTCSessionDescription(sdp=answer_sdp, type="answer")
                await pc.setRemoteDescription(answer)

        logger.info("WebRTC连接已建立")

        # 保持连接
        while pc.iceConnectionState not in ["failed", "disconnected", "closed"]:
            await asyncio.sleep(1)

    except Exception as e:
        logger.error(f"发生错误: {str(e)}", exc_info=True)
    finally:
        if pc:
            await pc.close()
            logger.info("WebRTC连接已关闭")


if __name__ == "__main__":
    # 设置更详细的日志
    logging.basicConfig(level=logging.DEBUG)

    # 运行推流
    asyncio.run(whip_publish_webrtc())