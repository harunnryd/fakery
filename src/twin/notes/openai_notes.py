from pydantic import BaseModel, Field

from twin.notes.summarizer import (
    SYSTEM_PROMPT,
    ActionItem,
    MeetingNotes,
    Segment,
    notes_prompt,
)

NOTES_TEMPERATURE = 0.2


class ActionItemSchema(BaseModel):
    text: str
    owner: str | None = None
    due: str | None = None


class NotesSchema(BaseModel):
    summary: str = ""
    key_points: list[str] = Field(default_factory=list)
    action_items: list[ActionItemSchema] = Field(default_factory=list)


def to_notes(schema: NotesSchema) -> MeetingNotes:
    return MeetingNotes(
        summary=schema.summary,
        key_points=list(schema.key_points),
        action_items=[
            ActionItem(text=item.text, owner=item.owner or None, due=item.due or None)
            for item in schema.action_items
            if item.text.strip()
        ],
    )


class OpenAINotes:
    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    async def summarize(self, segments: list[Segment]) -> MeetingNotes:
        from langchain_openai import ChatOpenAI

        model = ChatOpenAI(
            model=self._model, temperature=NOTES_TEMPERATURE, api_key=self._api_key
        ).with_structured_output(NotesSchema)
        result = await model.ainvoke([("system", SYSTEM_PROMPT), ("human", notes_prompt(segments))])
        return to_notes(result)
