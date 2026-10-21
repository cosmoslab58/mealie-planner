class MealiePlannerError(Exception):
    """Base class for exceptions in this module."""
    pass

class MealieAPIError(MealiePlannerError):
    """Exception raised for errors in the Mealie API communication."""
    def __init__(self, message, status_code=None, response_text=None):
        self.status_code = status_code
        self.response_text = response_text
        super().__init__(message)

class SkillParsingError(MealiePlannerError):
    """Exception raised for errors in parsing AI skill responses."""
    pass

class ConfigurationError(MealiePlannerError):
    """Exception raised for missing or invalid configuration."""
    pass

class AIResponseError(MealiePlannerError):
    """Exception raised when the AI returns an unusable response (refusal, truncation)."""
    pass
