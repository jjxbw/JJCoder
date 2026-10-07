from .agent import AgentTool
from .bash import BashTool
from .edit import EditFileTool
from .glob_tool import GlobTool
from .grep import GrepTool
from .project import ProjectDiagramTool, ProjectScanTool
from .read import ReadFileTool
from .todo import TodoWriteTool
from .web import WebFetchTool, WebSearchTool
from .write import WriteFileTool


ALL_TOOLS = [
    BashTool(),
    ReadFileTool(),
    WriteFileTool(),
    EditFileTool(),
    GlobTool(),
    GrepTool(),
    ProjectScanTool(),
    ProjectDiagramTool(),
    WebSearchTool(),
    WebFetchTool(),
    TodoWriteTool(),
    AgentTool(),
]
