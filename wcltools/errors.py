"""Errors that can safely be presented at the command boundary."""


class WCLError(Exception):
    def __init__(self, message: str, code: str = "invalid_input"):
        super().__init__(message)
        self.code = code

