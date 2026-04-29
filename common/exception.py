from common.error_code import ErrorCode


class AgentException(Exception):
    def __init__(self, error_code: ErrorCode):
        self.error_code = error_code
        self.message = error_code.message
        super().__init__(self.message)
