from enum import Enum


class CoverExtractStrategy(Enum):
    """封面提取策略枚举"""
    TIMESTAMP = "timestamp"  # 指定时间抽取
    CUSTOM = "custom"  # 自定义封面


class CoverTextConfig:
    """封面文字配置"""

    # 背景图尺寸
    BG_WIDTH = 1712
    BG_HEIGHT = 960

    # 标题/内容文字配置（回放名称）
    CONTENT_COLOR = "#236CFA"  # 蓝色
    CONTENT_FONT_SIZE = 100
    CONTENT_X = 128
    CONTENT_Y = 114

    # 信息文字配置（上课时间、所属空间）
    INFO_COLOR = "#8C8C8C"  # 灰色
    INFO_FONT_SIZE = 56
    INFO_X = 198

    TIME_Y = 600   # 上课时间 Y 坐标
    SPACE_Y = 720  # 所属空间 Y 坐标

    # 边框配置
    WHITE_COLOR = "white"
    BORDER_WIDTH = 2

    # 字体文件配置 (考虑跨平台兼容性)
    FONT_CONFIGS = {
        "windows": {
            "primary": "resource/fonts/PingFang.ttc",  # 项目内置字体
            "fallback": ["SimHei.ttf", "msyh.ttc", "simsun.ttc"]  # 系统字体
        },
        "linux": {
            "primary": "resource/fonts/PingFang.ttc",  # 项目内置字体
            "fallback": ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"]
        }
    }
