"""Router for mapping intents to downstream agents."""

from ..models.intent import IntentType
from ..agents.stubs import (
    BaseAgent,
    ReflectionAgent,
    KnowledgeGardenAgent,
    PlannerAgent,
    AnalystAgent,
    ToolAgent,
    NullAgent,
)


class IntentRouter:
    """Maps IntentType to downstream Agent instances."""
    
    def __init__(self):
        self._agents: dict[IntentType, BaseAgent] = {
            IntentType.REFLECT: ReflectionAgent(),
            IntentType.KNOWLEDGE_UPDATE: KnowledgeGardenAgent(),
            IntentType.TASK_GENERATION: PlannerAgent(),
            IntentType.DECISION_SUPPORT: AnalystAgent(),
            IntentType.EXECUTION: ToolAgent(),
            IntentType.IGNORE: NullAgent(),
        }
        
    def get_agent(self, intent_type: IntentType) -> BaseAgent:
        """Get the agent for a given intent type.
        
        Args:
            intent_type: The classified intent.
            
        Returns:
            The corresponding agent instance.
        """
        return self._agents.get(intent_type, NullAgent())
