from aiogram.fsm.state import State, StatesGroup


class SmartLinkFlow(StatesGroup):
    name = State()
    message = State()
    material_type = State()
    content = State()
    github_url = State()
    github_fallbacks = State()
    preview = State()


class ChannelFlow(StatesGroup):
    value = State()


class SourceFlow(StatesGroup):
    name = State()


class PostFlow(StatesGroup):
    content = State()
    button_choice = State()
    button_text = State()
    button_url = State()
    button_more = State()
    preview = State()
