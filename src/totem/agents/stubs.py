"""Agent stubs for downstream routing."""

from rich.console import Console

console = Console()


class BaseAgent:
    """Base class for agents."""
    
    def run(self, input_text: str) -> None:
        """Run the agent on the input text."""
        raise NotImplementedError


class ReflectionAgent(BaseAgent):
    """Stub for ReflectionAgent."""
    
    def run(self, input_text: str) -> None:
        console.print(f"[cyan]ReflectionAgent received:[/cyan] {input_text[:50]}...")


class KnowledgeGardenAgent(BaseAgent):
    """Stub for KnowledgeGardenAgent."""
    
    def run(self, input_text: str) -> None:
        console.print(f"[green]KnowledgeGardenAgent received:[/green] {input_text[:50]}...")


class PlannerAgent(BaseAgent):
    """Stub for PlannerAgent."""
    
    def run(self, input_text: str) -> None:
        console.print(f"[blue]PlannerAgent received:[/blue] {input_text[:50]}...")


class AnalystAgent(BaseAgent):
    """Stub for AnalystAgent."""
    
    def run(self, input_text: str) -> None:
        console.print(f"[magenta]AnalystAgent received:[/magenta] {input_text[:50]}...")


class ToolAgent(BaseAgent):
    """Stub for ToolAgent."""
    
    def run(self, input_text: str) -> None:
        console.print(f"[yellow]ToolAgent received:[/yellow] {input_text[:50]}...")


class NullAgent(BaseAgent):
    """Stub for NullAgent (ignore)."""
    
    def run(self, input_text: str) -> None:
        console.print(f"[dim]NullAgent ignoring:[/dim] {input_text[:50]}...")
