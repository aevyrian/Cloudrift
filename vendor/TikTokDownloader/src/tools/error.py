from ..translation import _


class RequestBlocked(RuntimeError):
    pass


class MediaHTTPError(RuntimeError):
    def __init__(self, status_code):
        self.status_code = status_code
        super().__init__(f"媒体服务器返回 HTTP {status_code}")


class DownloaderError(Exception):
    def __init__(
        self,
        message: str = "",
    ):
        self.message = message or _("项目代码错误")
        super().__init__(self.message)

    def __str__(self):
        return f"DownloaderError: {self.message}"


class CacheError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

    def __str__(self):
        return self.message
