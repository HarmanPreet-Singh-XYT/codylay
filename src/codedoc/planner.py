"""Planner — uses LLM to determine optimal file processing order."""

from typing import Dict, List, Any

from codedoc.llm_client import LLMClient
from codedoc.prompts import system_prompt, planning_prompt
from codedoc.state import AgentState


class Planner:
    def __init__(self, llm: LLMClient, config):
        self.llm = llm
        self.config = config

    def plan(
        self,
        file_tree: str,
        md_contents: Dict[str, str],
        files: List[str],
        state: AgentState,
    ) -> Dict[str, Any]:
        sys_prompt = system_prompt(self.config)
        user_prompt = planning_prompt(
            file_tree=file_tree,
            md_contents=md_contents,
            files=files,
            entry_hint=self.config.entry_hint,
        )
        result = self.llm.call(sys_prompt, user_prompt)
        return self._validate_plan(result, files)

    def _validate_plan(
        self, result: Dict[str, Any], all_files: List[str]
    ) -> Dict[str, Any]:
        all_files_set = set(all_files)

        # Validate order
        order = result.get("order", [])
        if not isinstance(order, list):
            order = list(all_files)
        else:
            order = [f for f in order if f in all_files_set]
            ordered_set = set(order)
            parked = result.get("parked", [])
            parked_set = set(parked) if isinstance(parked, list) else set()
            for f in all_files:
                if f not in ordered_set and f not in parked_set:
                    order.append(f)

        # Validate parked
        parked = result.get("parked", [])
        if not isinstance(parked, list):
            parked = []
        else:
            parked = [f for f in parked if f in all_files_set]

        # Validate park_reasons
        park_reasons = result.get("park_reasons", {})
        if not isinstance(park_reasons, dict):
            park_reasons = {}

        # Validate skeleton
        skeleton = result.get("skeleton", {})
        if not isinstance(skeleton, dict):
            skeleton = {}
        if "doc_title" not in skeleton:
            skeleton["doc_title"] = "Codebase Reference"
        if "suggested_sections" not in skeleton or not isinstance(
            skeleton["suggested_sections"], list
        ):
            skeleton["suggested_sections"] = [
                "Overview",
                "Entry Points",
                "Core Logic",
                "Data Models",
                "Utilities",
            ]

        return {
            "order": order,
            "parked": parked,
            "park_reasons": park_reasons,
            "skeleton": skeleton,
        }