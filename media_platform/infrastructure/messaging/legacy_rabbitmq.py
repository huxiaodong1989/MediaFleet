import pika
import pika.exceptions
import json
import logging
import time
from typing import Callable, Dict, Any, Optional, List
from threading import Thread, Lock, Event
from concurrent.futures import ThreadPoolExecutor



logger = logging.getLogger(__name__)


class RabbitMQMaster:
    """RabbitMQ 主节点。

    使用两条独立连接解决 pika.BlockingConnection 线程不安全问题：
    - _pub_conn/_pub_ch：专用于发布消息（由 asyncio/主线程通过 _pub_lock 串行操作）
    - _con_conn/_con_ch：专用于消费结果队列（由独立消费线程独占操作）

    消费线程在断线后会自动重连（指数退避），无需外部干预。
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 5672,
        username: str = "guest",
        password: str = "guest",
        exchange_name: str = "task_exchange",
        exchange_type: str = "topic",
    ):
        self.credentials = pika.PlainCredentials(username, password)
        self.parameters = pika.ConnectionParameters(
            host=host,
            port=port,
            credentials=self.credentials,
            heartbeat=60,                    # 60s 心跳，及时检测死连接
            blocked_connection_timeout=300,
            socket_timeout=10,               # socket 级超时，防止静默死连接
        )

        # 发布专用连接（主线程/asyncio 线程操作，受 _pub_lock 保护）
        self._pub_conn: Optional[pika.BlockingConnection] = None
        self._pub_ch = None
        self._pub_lock = Lock()

        # 消费专用连接（消费线程独占，禁止其他线程操作）
        self._con_conn: Optional[pika.BlockingConnection] = None
        self._con_ch = None
        self._consume_thread: Optional[Thread] = None
        self._stop_consuming = False

        self.result_queue = "result_queue"
        self.is_consuming = False
        self.exchange_name = exchange_name
        self.exchange_type = exchange_type

    # ------------------------------------------------------------------
    # 向后兼容属性：外部如仍直接访问 .connection / .channel 可正常工作
    # ------------------------------------------------------------------
    @property
    def connection(self):
        return self._pub_conn

    @property
    def channel(self):
        return self._pub_ch

    # ------------------------------------------------------------------
    # 内部连接管理
    # ------------------------------------------------------------------
    def _connect_publish(self):
        """建立/重建发布专用连接（调用方须持有 _pub_lock）"""
        try:
            if self._pub_conn and not self._pub_conn.is_closed:
                try:
                    self._pub_conn.close()
                except Exception:
                    pass
            self._pub_conn = pika.BlockingConnection(self.parameters)
            self._pub_ch = self._pub_conn.channel()
            self._pub_ch.exchange_declare(
                exchange=self.exchange_name,
                exchange_type=self.exchange_type,
                durable=True,
            )
            self._pub_ch.queue_declare(queue=self.result_queue, durable=True)
            logger.info(
                f"[publish-conn] Connected to RabbitMQ, exchange: {self.exchange_name}"
            )
        except Exception as e:
            logger.error(f"[publish-conn] Failed to connect: {e}")
            raise

    def _connect_consume(self):
        """建立/重建消费专用连接（仅在消费线程内调用）"""
        if self._con_conn and not self._con_conn.is_closed:
            try:
                self._con_conn.close()
            except Exception:
                pass
        self._con_conn = pika.BlockingConnection(self.parameters)
        self._con_ch = self._con_conn.channel()
        self._con_ch.exchange_declare(
            exchange=self.exchange_name,
            exchange_type=self.exchange_type,
            durable=True,
        )
        self._con_ch.queue_declare(queue=self.result_queue, durable=True)
        logger.info(
            f"[consume-conn] Connected to RabbitMQ, result_queue: {self.result_queue}"
        )

    def connect(self):
        """建立发布连接（供外部显式调用，线程安全）"""
        with self._pub_lock:
            self._connect_publish()

    def _get_routing_key(self, task_type: str) -> str:
        """根据任务类型生成路由键

        Args:
            task_type: 任务类型

        Returns:
            routing_key: 路由键字符串
        """
        # 任务类型到路由模式的映射
        type_mapping = {
            # 视频处理任务
            "video_extract_imgs": "video.extract",
            "video_extract_audio": "video.extract",
            "video_set_watermark": "video.process",
            "video_extract_cover": "video.extract",
            "video_extract_clips": "video.extract",

            # 音频处理任务
            "stream_extract_audio": "audio.extract",

            # 流处理任务
            "live_extract_cover": "stream.extract",

            # 识别任务
            "media_recog": "recognition.media",

            # 目标检测任务
            "obj_track_v_to_img": "object_detection.track",
            "obj_detect_img": "object_detection.detect",
        }

        # 默认使用任务类型的前缀作为路由键
        if task_type in type_mapping:
            return type_mapping[task_type]
        else:
            # 使用任务类型的第一个单词作为前缀
            prefix = task_type.split('_')[0] if '_' in task_type else task_type
            return f"{prefix}.default"

    def publish_task(self, task_data: Dict[str, Any], routing_key: Optional[str] = None):
        """发布任务到队列（线程安全，断线自动重连）

        Args:
            task_data: 任务数据字典
            routing_key: 可选的路由键，如果不提供则根据task_type自动生成
        """
        with self._pub_lock:
            if not self._pub_conn or self._pub_conn.is_closed:
                self._connect_publish()

            if routing_key is None:
                task_type = task_data.get("task_type", "")
                routing_key = self._get_routing_key(task_type)

            message = json.dumps(task_data)

            try:
                self._pub_ch.basic_publish(
                    exchange=self.exchange_name,
                    routing_key=routing_key,
                    body=message,
                    properties=pika.BasicProperties(
                        delivery_mode=2,
                        priority=task_data.get("priority", 0),
                    ),
                )
                logger.info(
                    f"Task published: task_id={task_data.get('task_id')}, "
                    f"routing_key={routing_key}, task_type={task_data.get('task_type')}"
                )
            except Exception as e:
                logger.error(f"Failed to publish task: {e}, reconnecting...")
                self._connect_publish()
                self._pub_ch.basic_publish(
                    exchange=self.exchange_name,
                    routing_key=routing_key,
                    body=message,
                    properties=pika.BasicProperties(
                        delivery_mode=2,
                        priority=task_data.get("priority", 0),
                    ),
                )
                logger.info("Task published after reconnection")

    def get_results(self, callback: Callable[[Dict[str, Any]], None]):
        """启动结果消费线程（幂等，线程存活时不重复启动）。

        消费线程在断线后会自动重连（指数退避 5s→10s→…→60s），
        无需外部重新调用本方法。

        Args:
            callback: 处理结果的回调函数
        """
        if self._consume_thread and self._consume_thread.is_alive():
            logger.debug("Consume thread is already running, skip")
            return

        self._stop_consuming = False

        def _result_callback(ch, method, properties, body):
            try:
                if body is None:
                    logger.warning("Received empty result")
                    return
                result = json.loads(body)
                logger.info(
                    "Master result callback received: task_id=%s, status=%s",
                    result.get("task_id"),
                    result.get("status"),
                )
                callback(result)
                ch.basic_ack(delivery_tag=method.delivery_tag)
            except Exception as e:
                logger.error(f"Error processing result: {e}")

        def consume_loop():
            retry_delay = 5
            while not self._stop_consuming:
                try:
                    self._connect_consume()
                    self.is_consuming = True
                    retry_delay = 5  # 成功连接后重置退避时间

                    self._con_ch.basic_consume(
                        queue=self.result_queue,
                        on_message_callback=_result_callback,
                    )
                    logger.info("[consume-conn] start_consuming")
                    self._con_ch.start_consuming()
                    logger.info("[consume-conn] start_consuming exited cleanly")

                except Exception as e:
                    logger.error(
                        f"[consume-conn] Error: {e}, retrying in {retry_delay}s..."
                    )
                finally:
                    self.is_consuming = False

                if not self._stop_consuming:
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 60)  # 指数退避，最长60s

        self._consume_thread = Thread(
            target=consume_loop, daemon=True, name="rmq-master-consume"
        )
        self._consume_thread.start()
        logger.info("Started result consume thread (auto-reconnect enabled)")

    def _do_publish_fanout(
        self,
        exchange_name: str,
        message_data: Dict[str, Any],
        ttl_milliseconds: Optional[int],
    ):
        """在已持有 _pub_lock 的情况下执行实际发布（内部方法）"""
        self._pub_ch.exchange_declare(
            exchange=exchange_name,
            exchange_type="fanout",
            durable=True,
        )
        message = json.dumps(message_data, ensure_ascii=False)
        properties = pika.BasicProperties(
            delivery_mode=2,
            content_type="application/json",
            content_encoding="utf-8",
        )
        if ttl_milliseconds:
            properties.expiration = str(ttl_milliseconds)
        self._pub_ch.basic_publish(
            exchange=exchange_name,
            routing_key="",
            body=message,
            properties=properties,
        )

    def publish_to_fanout_exchange(
        self,
        exchange_name: str,
        message_data: Dict[str, Any],
        ttl_milliseconds: Optional[int] = None,
    ):
        """发布消息到扇出交换机（线程安全，断线自动重连）

        Args:
            exchange_name: 扇出交换机名称
            message_data: 消息数据字典
            ttl_milliseconds: 消息TTL(毫秒),例如10小时=36000000毫秒
        """
        with self._pub_lock:
            if not self._pub_conn or self._pub_conn.is_closed:
                self._connect_publish()

            try:
                self._do_publish_fanout(exchange_name, message_data, ttl_milliseconds)
                logger.info(
                    f"Message published to fanout exchange: {exchange_name}, "
                    f"task_id={message_data.get('task_id')}, ttl={ttl_milliseconds}ms"
                )
            except Exception as e:
                logger.error(
                    f"Failed to publish message to fanout exchange: {e}, reconnecting..."
                )
                logger.info("Attempting to reconnect and republish to fanout exchange...")
                self._connect_publish()
                self._do_publish_fanout(exchange_name, message_data, ttl_milliseconds)
                logger.info("Message published to fanout exchange after reconnection")

    def close(self):
        """关闭所有连接并停止消费线程"""
        self._stop_consuming = True
        if self._con_ch:
            try:
                self._con_ch.stop_consuming()
            except Exception:
                pass
        if self._con_conn and not self._con_conn.is_closed:
            try:
                self._con_conn.close()
            except Exception:
                pass
        if self._pub_conn and not self._pub_conn.is_closed:
            try:
                self._pub_conn.close()
            except Exception:
                pass
        logger.info("RabbitMQ Master connections closed")


class RabbitMQWorker:
    def __init__(
        self,
        host: str = "localhost",
        port: int = 5672,
        username: str = "guest",
        password: str = "guest",
        max_tasks: int = 1,
        exchange_name: str = "task_exchange",
        exchange_type: str = "topic",
        queue_name: Optional[str] = None,
        routing_keys: Optional[list] = None,
        fanout_exchanges: Optional[List[str]] = None,
    ):
        """初始化RabbitMQ工作节点

        Args:
            host: RabbitMQ服务器地址
            port: RabbitMQ服务器端口
            username: RabbitMQ用户名
            password: RabbitMQ密码
            max_tasks: 最大并发任务数
            exchange_name: Exchange名称
            exchange_type: Exchange类型
            queue_name: 队列名称
            routing_keys: 路由键列表，用于绑定队列到Exchange
                          例如: ["video.*", "audio.extract"]
            fanout_exchanges: 需要预声明的 fanout exchange 名称列表，
                              在 connect() 时一次性声明，避免在 I/O 回调中执行同步 RPC
        """
        if not queue_name:
            raise ValueError("queue_name must be specified to ensure proper task distribution")

        self.credentials = pika.PlainCredentials(username, password)
        self.parameters = pika.ConnectionParameters(
            host=host,
            port=port,
            credentials=self.credentials,
            heartbeat=120,
            blocked_connection_timeout=300,
        )
        self.connection = None
        self.channel = None
        self.exchange_name = exchange_name
        self.exchange_type = exchange_type
        self.queue_name = queue_name
        self.routing_keys = routing_keys or []
        self.fanout_exchanges = fanout_exchanges or []
        self.result_queue = "result_queue"
        self.executor = ThreadPoolExecutor(max_workers=max_tasks)
        self.max_tasks = max_tasks
        self._processing_tasks = set()
        self._processing_lock = Lock()

    def connect(self):
        """建立与RabbitMQ服务器的连接"""
        try:
            if self.connection and not self.connection.is_closed:
                try:
                    self.connection.close()
                except Exception:
                    pass
            self.connection = pika.BlockingConnection(self.parameters)
            self.channel = self.connection.channel()

            # 声明任务队列
            # self.channel.queue_declare(queue=self.task_queue, durable=True)
            # 声明Exchange（确保存在）
            self.channel.exchange_declare(
                exchange=self.exchange_name,
                exchange_type=self.exchange_type,
                durable=True
            )

            # 声明结果队列
            # self.channel.queue_declare(queue=self.result_queue, durable=True)

            # 声明队列（持久化）
            self.channel.queue_declare(
                queue=self.queue_name,
                durable=True,
                exclusive=False,  # 允许其他连接访问
                auto_delete=False  # 队列不自动删除
            )

            # 将队列绑定到Exchange（支持多个路由键）
            for routing_key in self.routing_keys:
                self.channel.queue_bind(
                    exchange=self.exchange_name,
                    queue=self.queue_name,
                    routing_key=routing_key
                )
                logger.info(f"Queue {self.queue_name} bound to {self.exchange_name} "
                          f"with routing_key: {routing_key}")

            # 声明结果队列
            self.channel.queue_declare(queue=self.result_queue, durable=True)

            # 预声明 fanout exchanges，避免在 add_callback_threadsafe 回调中执行同步 RPC
            for fanout_ex in self.fanout_exchanges:
                self.channel.exchange_declare(
                    exchange=fanout_ex,
                    exchange_type='fanout',
                    durable=True
                )
                logger.info(f"Fanout exchange declared: {fanout_ex}")

            self.channel.basic_qos(prefetch_count=self.max_tasks)

            # logger.info(f"Successfully connected to RabbitMQ server, max_tasks: {self.max_tasks}")
            logger.info(f"Worker connected: queue={self.queue_name}, "
                       f"routing_keys={self.routing_keys}, max_tasks={self.max_tasks}")
        except Exception as e:
            logger.error(f"Failed to connect to RabbitMQ server: {str(e)}")
            raise

    def start_consuming(self, task_handler: Callable[[Dict[str, Any]], Dict[str, Any]]):
        """开始消费任务

        Args:
            task_handler: 处理任务的回调函数，接收任务数据并返回处理结果
        """
        # 检查连接状态，如果连接关闭则重新连接
        logger.info("----------------Worker start_consuming--------------------")
        # 使用 getattr 获取属性并提供默认值，或者使用条件判断
        conn_closed = self.connection.is_closed if self.connection else True
        chan_closed = self.channel.is_closed if self.channel else True

        logger.info(f"Connection is closed: {conn_closed}")
        logger.info(f"Channel is closed: {chan_closed}")

        # 对于 executor，通常其内部属性名为 _shutdown 或通过方法判断
        # 如果是 ThreadPoolExecutor，属性通常是 _shutdown
        executor_shutdown = getattr(self.executor, '_shutdown', True)
        logger.info(f"Executor is shutdown: {executor_shutdown}")
        logger.info("------------------------------------------------")

        if not self.connection or not self.channel or self.connection.is_closed or self.channel.is_closed:
            logger.error(
                "Connection is not established or closed, reconnecting..."
            )
            self.connect()

        def task_callback(ch, method, properties, body):
            try:
                task_data = json.loads(body)
            except json.JSONDecodeError as e:
                logger.error(f"Invalid task payload: {e}, body={body}")
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                return

            task_id = task_data.get('task_id')
            delivery_tag = method.delivery_tag

            with self._processing_lock:
                if task_id in self._processing_tasks:
                    logger.warning(f"Duplicate task skipped (already processing): task_id={task_id}")
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                    return
                self._processing_tasks.add(task_id)

            # 捕获当前 channel 和 connection 的引用。
            # 重连后 self.channel / self.connection 会指向新对象，
            # 但旧任务仍需使用旧引用来 ack/nack（旧 delivery_tag 只在旧 channel 上有效）。
            captured_ch = ch
            captured_conn = self.connection

            logger.info(f"Worker received task: task_id={task_id}, "
                       f"task_type={task_data.get('task_type')}, "
                       f"routing_key={method.routing_key}")

            def on_task_completed(result):
                try:
                    if captured_ch.is_closed:
                        logger.warning(f"Original channel already closed, skip ack for task_id={task_id}. "
                                      "Message will be requeued by broker automatically.")
                        return
                    if result:
                        captured_ch.basic_publish(
                            exchange="",
                            routing_key=self.result_queue,
                            body=json.dumps(result),
                            properties=pika.BasicProperties(
                                delivery_mode=2,
                            ),
                        )
                    captured_ch.basic_ack(delivery_tag=delivery_tag)
                    logger.info(f"Task completed and acknowledged: task_id={task_id}")
                except Exception as e:
                    logger.error(f"Failed to publish/ack result in main thread: {e}")

            def on_task_failed():
                try:
                    if captured_ch.is_closed:
                        logger.warning(f"Original channel already closed, skip nack for task_id={task_id}. "
                                      "Message will be requeued by broker automatically.")
                        return
                    captured_ch.basic_nack(delivery_tag=delivery_tag, requeue=True)
                    logger.info(f"Task failed and nacked: task_id={task_id}")
                except Exception as e:
                    logger.error(f"Failed to nack message in main thread: {e}")

            def run_task():
                try:
                    result = task_handler(task_data)
                    if captured_conn.is_closed:
                        logger.warning(f"Connection closed before ack scheduling for task_id={task_id}")
                        return
                    captured_conn.add_callback_threadsafe(lambda: on_task_completed(result))
                except Exception as task_err:
                    logger.error(f"Error processing task {task_id}: {task_err}", exc_info=True)
                    try:
                        if not captured_conn.is_closed:
                            captured_conn.add_callback_threadsafe(on_task_failed)
                        else:
                            logger.warning(f"Connection closed, cannot nack task_id={task_id}")
                    except Exception as cb_err:
                        logger.error(f"Failed to schedule nack callback for task_id={task_id}: {cb_err}")
                finally:
                    with self._processing_lock:
                        self._processing_tasks.discard(task_id)

            self.executor.submit(run_task)

        self.channel.basic_consume(
            queue=self.queue_name,
            on_message_callback=task_callback
        )

        try:
            logger.info(f"Worker started consuming tasks from queue: {self.queue_name}")
            self.channel.start_consuming()
        except pika.exceptions.ChannelClosedByBroker as e:
            logger.error(f"Channel closed by broker: reply_code={e.reply_code}, "
                        f"reply_text={e.reply_text}")
            raise
        except pika.exceptions.ChannelClosed as e:
            logger.error(f"Channel closed unexpectedly: {e}")
            raise
        except pika.exceptions.ConnectionClosedByBroker as e:
            logger.error(f"Connection closed by broker: reply_code={e.reply_code}, "
                        f"reply_text={e.reply_text}")
            raise
        except pika.exceptions.ConnectionClosed as e:
            logger.error(f"Connection closed unexpectedly: {e}")
            raise
        except KeyboardInterrupt:
            self.channel.stop_consuming()


    def publish_to_fanout_exchange(
        self,
        exchange_name: str,
        message_data: Dict[str, Any],
        ttl_milliseconds: Optional[int] = None
    ):
        """发布消息到扇出交换机

        Args:
            exchange_name: 扇出交换机名称
            message_data: 消息数据字典
            ttl_milliseconds: 消息TTL(毫秒),例如10小时=36000000毫秒
        """
        # 检查连接状态，如果连接关闭则重新连接
        if not self.connection or self.connection.is_closed:
            logger.info("Connection is closed, reconnecting...")
            self.connect()

        try:
            # 确保扇出交换机存在
            self.channel.exchange_declare(
                exchange=exchange_name,
                exchange_type='fanout',
                durable=True  # 持久化交换机
            )

            message = json.dumps(message_data, ensure_ascii=False)

            # 设置消息属性
            properties = pika.BasicProperties(
                delivery_mode=2,  # 消息持久化
                content_type='application/json',
                content_encoding='utf-8'
            )

            # 如果指定了TTL，添加到属性中
            if ttl_milliseconds:
                properties.expiration = str(ttl_milliseconds)

            # 发布到扇出交换机（routing_key为空字符串，因为fanout类型会忽略routing_key）
            self.channel.basic_publish(
                exchange=exchange_name,
                routing_key='',  # fanout类型交换机忽略routing_key
                body=message,
                properties=properties
            )

            logger.info(f"Message published to fanout exchange: {exchange_name}, "
                       f"task_id={message_data.get('task_id')}, "
                       f"ttl={ttl_milliseconds}ms")

        except Exception as e:
            logger.error(f"Failed to publish message to fanout exchange: {str(e)}")
            # 尝试重新连接并再次发布
            try:
                logger.info("Attempting to reconnect and republish to fanout exchange...")
                self.connect()

                # 重新声明交换机
                self.channel.exchange_declare(
                    exchange=exchange_name,
                    exchange_type='fanout',
                    durable=True
                )

                message = json.dumps(message_data, ensure_ascii=False)
                properties = pika.BasicProperties(
                    delivery_mode=2,
                    content_type='application/json',
                    content_encoding='utf-8'
                )

                if ttl_milliseconds:
                    properties.expiration = str(ttl_milliseconds)

                self.channel.basic_publish(
                    exchange=exchange_name,
                    routing_key='',
                    body=message,
                    properties=properties
                )
                logger.info("Message published to fanout exchange after reconnection")
            except Exception as e2:
                logger.error(f"Failed to republish message to fanout exchange: {str(e2)}")
                raise


    def publish_to_fanout_exchange_threadsafe(
        self,
        exchange_name: str,
        message_data: Dict[str, Any],
        ttl_milliseconds: Optional[int] = None,
        timeout: float = 10.0
    ):
        """线程安全地发布消息到扇出交换机，可从任意线程调用。

        通过 add_callback_threadsafe 将 channel 操作调度到 pika IO 线程执行，
        避免多线程并发访问 channel 导致内部状态损坏。

        Args:
            exchange_name: 扇出交换机名称
            message_data: 消息数据字典
            ttl_milliseconds: 消息TTL(毫秒)
            timeout: 等待发布完成的超时时间(秒)
        """
        if not self.connection or self.connection.is_closed:
            raise Exception("RabbitMQ connection is not available for threadsafe publish")

        error_holder: list = [None]
        done_event = Event()

        def _do_publish():
            try:
                # 不要在回调中调用 exchange_declare 等同步 RPC，
                # 它会在 pika I/O 循环中创建嵌套事件处理，导致 channel 被关闭。
                # exchange 已在 connect() 中预先声明。
                message = json.dumps(message_data, ensure_ascii=False)
                properties = pika.BasicProperties(
                    delivery_mode=2,
                    content_type='application/json',
                    content_encoding='utf-8'
                )

                if ttl_milliseconds:
                    properties.expiration = str(ttl_milliseconds)

                self.channel.basic_publish(
                    exchange=exchange_name,
                    routing_key='',
                    body=message,
                    properties=properties
                )

                logger.info(f"Message published to fanout exchange (threadsafe): {exchange_name}, "
                           f"task_id={message_data.get('task_id')}, "
                           f"ttl={ttl_milliseconds}ms")
            except Exception as e:
                error_holder[0] = e
                logger.error(f"Failed to publish message to fanout exchange (threadsafe): {str(e)}")
            finally:
                done_event.set()

        self.connection.add_callback_threadsafe(_do_publish)

        if not done_event.wait(timeout=timeout):
            raise TimeoutError(f"Publish to fanout exchange '{exchange_name}' timed out after {timeout}s")

        if error_holder[0]:
            raise error_holder[0]

    def close(self):
        """关闭连接"""
        if self.executor:
            self.executor.shutdown(wait=False)
        if self.connection and not self.connection.is_closed:
            self.connection.close()
            logger.info("RabbitMQ connection closed")
