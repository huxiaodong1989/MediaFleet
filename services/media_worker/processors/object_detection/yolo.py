import asyncio
import math
import os
from collections import defaultdict
import time
from media_platform.infrastructure.storage import get_enhanced_storage_service
from media_platform.common.redaction import sanitize_url
import logging
from pathlib import Path
from typing import Dict, Any

logger = logging.getLogger("object_detection")

PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_YOLO_MODEL_PATH = (
    PROJECT_ROOT / "resource" / "models" / "yolo" / "yolov8n_head_100.pt"
)

class Tracker:
    """目标跟踪器"""
    def __init__(self, max_disappeared=30, keep_all_tracks=False):
        self.next_object_id = 0
        self.objects = {}
        self.disappeared = {}
        self.max_disappeared = max_disappeared
        self.tracks = defaultdict(list)  # 存储轨迹点
        self.keep_all_tracks = keep_all_tracks  # 是否保留所有历史轨迹
        self.all_tracks = defaultdict(list)  # 存储所有历史轨迹（即使目标已消失）

    def register(self, centroid):
        """注册新的目标"""
        self.objects[self.next_object_id] = centroid
        self.disappeared[self.next_object_id] = 0
        self.tracks[self.next_object_id].append(centroid)
        self.all_tracks[self.next_object_id].append(centroid)
        self.next_object_id += 1

    def deregister(self, object_id):
        """注销目标"""
        del self.objects[object_id]
        del self.disappeared[object_id]
        # 如果不需要保留所有轨迹，则删除
        if not self.keep_all_tracks and object_id in self.tracks:
            del self.tracks[object_id]

    def update(self, detections):
        """更新跟踪器状态"""
        if len(detections) == 0:
            # 如果没有检测到目标，增加所有目标的消失计数
            for object_id in list(self.disappeared.keys()):
                self.disappeared[object_id] += 1
                if self.disappeared[object_id] > self.max_disappeared:
                    self.deregister(object_id)
            return self.objects

        if len(self.objects) == 0:
            # 如果当前没有跟踪的目标，注册所有检测到的目标
            for detection in detections:
                self.register(detection)
        else:
            # 计算当前检测到的目标与已跟踪目标的距离
            object_ids = list(self.objects.keys())
            object_centroids = list(self.objects.values())

            # 计算欧几里得距离
            distances = []
            for detection in detections:
                dist = []
                for centroid in object_centroids:
                    dist.append(
                        math.hypot(
                            detection[0] - centroid[0],
                            detection[1] - centroid[1],
                        )
                    )
                distances.append(dist)

            # 简单的最近邻匹配
            used_detections = set()
            used_objects = set()

            for i, detection in enumerate(detections):
                if i in used_detections:
                    continue

                min_dist = float('inf')
                min_idx = -1

                for j, object_id in enumerate(object_ids):
                    if j in used_objects:
                        continue
                    if distances[i][j] < min_dist and distances[i][j] < 100:  # 最大距离阈值
                        min_dist = distances[i][j]
                        min_idx = j

                if min_idx != -1:
                    object_id = object_ids[min_idx]
                    self.objects[object_id] = detection
                    self.disappeared[object_id] = 0
                    self.tracks[object_id].append(detection)
                    self.all_tracks[object_id].append(detection)
                    used_detections.add(i)
                    used_objects.add(min_idx)
                else:
                    # 没有匹配的目标，注册新的
                    self.register(detection)

            # 处理未匹配的已跟踪目标
            for j, object_id in enumerate(object_ids):
                if j not in used_objects:
                    self.disappeared[object_id] += 1
                    if self.disappeared[object_id] > self.max_disappeared:
                        self.deregister(object_id)

        return self.objects

class ObjectDetection:
    def __init__(self, model_path=None):
        self.storage_service = get_enhanced_storage_service()
        configured_path = model_path or os.getenv(
            "YOLO_MODEL_PATH", str(DEFAULT_YOLO_MODEL_PATH)
        )
        resolved_path = Path(configured_path)
        if not resolved_path.is_absolute():
            resolved_path = PROJECT_ROOT / resolved_path
        if not resolved_path.is_file():
            raise FileNotFoundError(f"YOLO模型文件不存在: {resolved_path}")
        from ultralytics import YOLO

        self.model = YOLO(str(resolved_path))
        self.coco_classes = {
            0: 'person', 1: 'bicycle', 2: 'car', 3: 'motorcycle', 4: 'airplane', 5: 'bus',
            6: 'train', 7: 'truck', 8: 'boat', 9: 'traffic light', 10: 'fire hydrant',
            11: 'stop sign', 12: 'parking meter', 13: 'bench', 14: 'bird', 15: 'cat',
            16: 'dog', 17: 'horse', 18: 'sheep', 19: 'cow', 20: 'elephant',
            21: 'bear', 22: 'zebra', 23: 'giraffe', 24: 'backpack', 25: 'umbrella',
            26: 'handbag', 27: 'tie', 28: 'suitcase', 29: 'frisbee', 30: 'skis',
            31: 'snowboard', 32: 'sports ball', 33: 'kite', 34: 'baseball bat', 35: 'baseball glove',
            36: 'skateboard', 37: 'surfboard', 38: 'tennis racket', 39: 'bottle', 40: 'wine glass',
            41: 'cup', 42: 'fork', 43: 'knife', 44: 'spoon', 45: 'bowl',
            46: 'banana', 47: 'apple', 48: 'sandwich', 49: 'orange', 50: 'broccoli',
            51: 'carrot', 52: 'hot dog', 53: 'pizza', 54: 'donut', 55: 'cake',
            56: 'chair', 57: 'couch', 58: 'potted plant', 59: 'bed', 60: 'dining table',
            61: 'toilet', 62: 'tv', 63: 'laptop', 64: 'mouse', 65: 'remote',
            66: 'keyboard', 67: 'cell phone', 68: 'microwave', 69: 'oven', 70: 'toaster',
            71: 'sink', 72: 'refrigerator', 73: 'book', 74: 'clock', 75: 'vase',
            76: 'scissors', 77: 'teddy bear', 78: 'hair drier', 79: 'toothbrush',
            80: 'head',
        }

    # 生成视频中目标对象的轨迹图片
    async def generate_trajectory_video_to_img(self, video_url, output_img_path="output/trajectory.png", target_classes=None):
        """
        处理视频文件，生成包含所有目标轨迹的PNG图片

        Args:
            video_path (str): 输入视频文件路径
            output_img_path (str): 输出PNG图片路径
            target_classes (list): 要跟踪的目标类别列表，如果为None则跟踪所有类别
        """
        import cv2
        import numpy as np
        import supervision as sv

        # 下载视频文件
        video_path=await self.storage_service.download_file(video_url)
        logger.info(f"文件下载成功:{video_path}")

        if target_classes is None:
            target_classes = self.coco_classes.values()

        # 加载模型
        logger.info("正在加载模型...")
        model = self.model

        # 打开视频文件
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            logger.error(f"错误：无法打开视频文件 {video_path}")
            raise ValueError(f"错误：无法打开视频文件 {video_path}")

        # 获取视频属性
        fps = int(cap.get(cv2.CAP_PROP_FPS))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        logger.info(f"视频信息：{width}x{height}, {fps}fps, 总帧数：{total_frames}")
        logger.info(f"跟踪目标类别: {target_classes}")

        # 创建跟踪器（保留所有历史轨迹）
        tracker = Tracker(max_disappeared=30, keep_all_tracks=True)

        # 获取目标类别ID
        target_class_ids = [class_id for class_id, class_name in self.coco_classes.items()
                            if class_name in target_classes]

        # 处理每一帧，收集轨迹数据
        frame_count = 0
        start_time = time.time()
        first_frame = None

        logger.info("正在处理视频并收集轨迹数据...")

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            # 保存第一帧作为背景
            if frame_count == 0:
                first_frame = frame.copy()

            frame_count += 1
            if frame_count % 30 == 0:  # 每30帧打印一次进度
                elapsed_time = time.time() - start_time
                fps_processed = frame_count / elapsed_time
                progress = (frame_count / total_frames) * 100
                print(f"处理进度: {progress:.1f}% ({frame_count}/{total_frames}帧) - 速度: {fps_processed:.1f} fps")

            # 进行推理
            results = model(frame)[0]
            detections = sv.Detections.from_ultralytics(results)

            # 过滤目标类别
            if target_class_ids:
                class_filter = np.isin(detections.class_id, target_class_ids)
                detections = detections[class_filter]

            # 计算检测框的中心点
            centroids = []
            for bbox in detections.xyxy:
                x1, y1, x2, y2 = bbox
                centroid = (int((x1 + x2) / 2), int((y1 + y2) / 2))
                centroids.append(centroid)

            # 更新跟踪器
            tracker.update(centroids)

        # 清理视频资源
        cap.release()

        # 创建轨迹图片（使用第一帧作为背景，半透明处理）
        logger.info("正在生成轨迹图片...")
        trajectory_image = first_frame.copy()

        # 将背景图片调暗以突出轨迹
        trajectory_image = cv2.addWeighted(trajectory_image, 0.3, np.zeros_like(trajectory_image), 0.7, 0)

        # 为每个目标ID生成不同的颜色
        np.random.seed(42)  # 固定随机种子以获得一致的颜色
        colors = {}
        for object_id in tracker.all_tracks.keys():
            colors[object_id] = (
                int(np.random.randint(50, 255)),
                int(np.random.randint(50, 255)),
                int(np.random.randint(50, 255))
            )

        # 绘制所有轨迹
        track_count = 0
        for object_id, track in tracker.all_tracks.items():
            if len(track) > 1:
                track_count += 1
                color = colors[object_id]

                # 绘制轨迹线
                for i in range(1, len(track)):
                    cv2.line(trajectory_image, track[i-1], track[i], color, 3)

                # 在起点绘制圆圈
                cv2.circle(trajectory_image, track[0], 8, color, -1)
                cv2.circle(trajectory_image, track[0], 10, (255, 255, 255), 2)

                # 在终点绘制箭头
                if len(track) >= 2:
                    cv2.arrowedLine(trajectory_image, track[-2], track[-1], color, 3, tipLength=0.3)

                # 在起点标注ID
                cv2.putText(trajectory_image, f"ID:{object_id}",
                        (track[0][0] + 15, track[0][1] - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

        # 添加标题和统计信息
        info_bg = np.zeros((120, width, 3), dtype=np.uint8)
        cv2.putText(info_bg, "Object Trajectory Map",
                (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 2)
        cv2.putText(info_bg, f"Video: {os.path.basename(video_path)}",
                (20, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 2)
        cv2.putText(info_bg, f"Total Frames: {total_frames} | Tracked Objects: {track_count} | Target Classes: {', '.join(target_classes)}",
                (20, 95), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)

        # 将信息区域与轨迹图片合并
        final_image = np.vstack([info_bg, trajectory_image])

        # 保存图片
        cv2.imwrite(output_img_path, final_image)

        logger.info(f"\n轨迹图片已保存到: {output_img_path}")
        logger.info(f"总共跟踪了 {track_count} 个目标")
        logger.info(f"处理时间: {time.time() - start_time:.2f} 秒")

        # 上传片段到存储服务
        logger.info("开始上传轨迹图片到存储服务")
        upload_result = await self.storage_service.upload_file_enhanced(
            output_img_path,
            upload_type="image"
        )
        if upload_result and upload_result.file_url:
            logger.info(f"图片 {output_img_path} 上传成功: {upload_result.file_url}")
            return upload_result.file_url
        else:
            logger.error(f"图片 {output_img_path} 上传失败")
            return None

    async def object_detection(self, img_url, output_img_path="output/detection.png", target_classes=None):
        """
        处理图片，生成包含所有目标检测的PNG图片
        """
        import cv2
        import numpy as np
        import supervision as sv

        img_path=await self.storage_service.download_file(img_url)
        logger.info(f"文件下载成功:{img_path}")

        if target_classes is None:
            target_classes = self.coco_classes.values()
        logger.info(f"目标检测类别: {target_classes}")
        # 加载模型
        logger.info("正在加载模型...")
        model = self.model

        # 读取图片
        image = cv2.imread(img_path)

        # 推理
        results = model(image)[0]
        logger.info(f"目标检测结果: {results}")
        detections = sv.Detections.from_ultralytics(results)  # 转换输出格式

        # 过滤检测结果
        # 获取目标类别ID
        target_class_ids = [class_id for class_id, class_name in self.coco_classes.items()
                            if class_name in target_classes]

        # 根据target_classes过滤检测结果
        if target_class_ids:
            class_filter = np.isin(detections.class_id, target_class_ids)
            detections = detections[class_filter]
            logger.info(f"过滤后检测到的目标数量: {len(detections)}")
        else:
            logger.warning("未找到匹配的目标类别，将显示所有检测结果")

        # 绘制边界框和标签
        box_annotator = sv.BoxAnnotator()
        label_annotator = sv.LabelAnnotator()

        # 创建标签
        labels = [f"{model.model.names[class_id]} {confidence:.2f}"
                for class_id, confidence in zip(detections.class_id, detections.confidence)]

        # 绘制边界框
        annotated_image = box_annotator.annotate(
            scene=image.copy(),
            detections=detections
        )

        # 添加标签
        annotated_image = label_annotator.annotate(
            scene=annotated_image,
            detections=detections,
            labels=labels
        )

        # 保存结果
        cv2.imwrite(output_img_path, annotated_image)
        logger.info(f"目标检测图片已保存到: {output_img_path}")
        upload_result = await self.storage_service.upload_file_enhanced(
            output_img_path,
            upload_type="image"
        )
        if upload_result and upload_result.file_url:
            logger.info(f"图片 {output_img_path} 上传成功: {upload_result.file_url}")
            return upload_result.file_url
        else:
            logger.error(f"图片 {output_img_path} 上传失败")
            return None




    # 回调通知
    async def callback_notification(
        self, task_id: str, callback_url: str, result: Dict[str, Any]
    ) -> bool:
        """
        发送回调通知
        :param task_id: 任务ID
        :param result: 任务结果
        :return: 是否发送成功
        """
        try:
            if not callback_url:
                logger.info(f"没有设置回调URL，跳过回调: {task_id}")
                return False

            logger.info("回调数据已构造: task_id=%s", task_id)
            # 发送回调请求，支持重试
            max_retries = 3
            retry_interval = 5  # 秒

            for retry in range(max_retries):
                try:
                    logger.info(
                        "开始发送回调通知(尝试 %s/%s): URL=%s, 任务ID=%s",
                        retry + 1,
                        max_retries,
                        sanitize_url(callback_url),
                        task_id,
                    )

                    # 发送HTTP POST请求
                    response = await self.http_client.post(
                        callback_url,
                        json=result,
                        headers={"Content-Type": "application/json"},
                        timeout=30.0,
                    )

                    # 检查响应状态
                    if response.status_code < 300:
                        logger.info(f"回调通知发送成功: {task_id}")
                        return True
                    else:
                        logger.warning(
                            f"回调通知发送失败(尝试 {retry + 1}/{max_retries}): {task_id}, 状态码: {response.status_code}, 响应: {response.text}"
                        )

                        # 最后一次重试失败
                        if retry == max_retries - 1:
                            logger.error(
                                f"回调通知达到最大重试次数，放弃发送: {task_id}"
                            )
                            return False

                        # 等待一段时间后重试
                        await asyncio.sleep(retry_interval)

                except Exception as e:
                    logger.warning(
                        f"回调通知异常(尝试 {retry + 1}/{max_retries}): {task_id}, 错误: {str(e)}"
                    )

                    # 最后一次重试失败
                    if retry == max_retries - 1:
                        logger.error(f"回调通知达到最大重试次数，放弃发送: {task_id}")
                        return False

                    # 等待一段时间后重试
                    await asyncio.sleep(retry_interval)

            return False

        except Exception as e:
            logger.error(f"发送回调通知异常: {task_id}, 错误: {str(e)}", exc_info=True)
            return False
