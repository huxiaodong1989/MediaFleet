from media_platform.application.ports import PublishReceipt
from media_platform.contracts.task import TaskDeliveryChannel, TaskDispatchMessage
from media_platform.infrastructure.messaging import ChannelTaskPublisher


class Publisher:
    def __init__(self):
        self.messages = []

    def publish(self, message):
        self.messages.append(message)
        return PublishReceipt(message_id=message.message_id)


def test_channel_publisher_routes_content_tasks_away_from_media_exchange():
    media = Publisher()
    content = Publisher()
    image = Publisher()
    publisher = ChannelTaskPublisher(
        {
            TaskDeliveryChannel.MEDIA: media,
            TaskDeliveryChannel.CONTENT_ANALYSIS: content,
            TaskDeliveryChannel.IMAGE_ANALYSIS: image,
        }
    )
    message = TaskDispatchMessage(
        message_id="message-1",
        task_id="task-1",
        school_code="school-1",
        task_type="content.class_evaluation",
        routing_key="content.class_evaluation",
        delivery_channel=TaskDeliveryChannel.CONTENT_ANALYSIS,
    )

    receipt = publisher.publish(message)

    assert receipt.message_id == "message-1"
    assert media.messages == []
    assert content.messages == [message]
    assert image.messages == []

    image_message = message.model_copy(
        update={
            "task_type": "image.three_rates.analyze",
            "routing_key": "image.three_rates.analyze",
            "delivery_channel": TaskDeliveryChannel.IMAGE_ANALYSIS,
        }
    )
    assert publisher.publish(image_message).message_id == "message-1"
    assert image.messages == [image_message]
    assert media.messages == []
