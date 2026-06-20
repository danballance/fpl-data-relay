from pydantic import BaseModel, ConfigDict


class UpstreamModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class Event(UpstreamModel):
    id: int
    name: str
    is_current: bool


class Team(UpstreamModel):
    id: int
    name: str
    short_name: str


class Element(UpstreamModel):
    id: int
    first_name: str
    second_name: str
    web_name: str
    team: int
    element_type: int


class ElementType(UpstreamModel):
    id: int
    singular_name: str
    plural_name: str


class BootstrapStatic(UpstreamModel):
    events: list[Event]
    teams: list[Team]
    elements: list[Element]
    element_types: list[ElementType]


class Fixture(UpstreamModel):
    id: int
    team_h: int
    team_a: int
    started: bool
    finished: bool


class EventStatusDay(UpstreamModel):
    event: int
    bonus_added: bool
    date: str
    leagues_updated: bool


class EventStatusResponse(UpstreamModel):
    status: list[EventStatusDay]


class LiveElementStats(UpstreamModel):
    total_points: int


class LiveElementExplainStat(UpstreamModel):
    identifier: str
    points: int


class LiveElementExplain(UpstreamModel):
    fixture: int
    stats: list[LiveElementExplainStat]


class LiveElement(UpstreamModel):
    id: int
    stats: LiveElementStats
    explain: list[LiveElementExplain]


class EventLiveResponse(UpstreamModel):
    elements: list[LiveElement]

