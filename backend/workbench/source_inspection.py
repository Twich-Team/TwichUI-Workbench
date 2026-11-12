"""Broader read-only bug navigation, separate from frozen Lua execution schemas."""
from typing import Annotated

from pydantic import Field

from workbench.bug_evidence import InvestigationRead as LuaRead, InvestigationSearch as LuaSearch
from workbench.source_artifacts import PATTERN


class InvestigationRead(LuaRead):
    path: Annotated[str, Field(pattern=PATTERN, max_length=240)]


class InvestigationSearch(LuaSearch):
    path: Annotated[str, Field(max_length=240, pattern=r'^(?:!!!TwichUI|TwichUI_Settings|tests|tools|assets)(?:/[A-Za-z0-9_!-][A-Za-z0-9_.!-]*)*$')] | None = None
