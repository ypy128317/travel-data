"""旅游知识库：解析 Markdown 攻略文档，切片建索引，提供检索能力。

数据来源：E:/BaiduNetdiskDownload/旅游/数据（6 城市 × 5 类攻略）。
设计原则：
- 启动时一次性加载到内存（总量约 15 万字，很轻）；
- 单个文件解析失败只记录警告，不影响整体服务（异常隔离）；
- 检索返回带出处（城市/内容类型/景点或线路名/来源文件），可追溯。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

# 知识库数据目录，按优先级解析：
# 1) 环境变量 KB_DATA_DIR（容器/服务器部署时指定）
# 2) 项目根目录下的 kb_data（随项目一起分发，容器内为 /app/kb_data）
# 3) 本地开发时的原始资料路径
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_DIR = os.environ.get("KB_DATA_DIR")
if _ENV_DIR:
    DEFAULT_DATA_DIR = Path(_ENV_DIR)
elif (_PROJECT_ROOT / "kb_data").is_dir():
    DEFAULT_DATA_DIR = _PROJECT_ROOT / "kb_data"
else:
    DEFAULT_DATA_DIR = Path("E:/BaiduNetdiskDownload/旅游/数据")

# 内容类型目录 → 统一类型名
TYPE_DIRS = {
    "景点攻略": "景点介绍",
    "线路推荐": "线路推荐",
    "酒店信息": "酒店信息",
    "美食推荐": "美食推荐",
    "交通指南": "交通指南",
}

_META_RE = re.compile(r"^-\s*([^：:]+)[：:]\s*(.+)$")
_HEAD_RE = re.compile(r"^(#{2,4})\s+(.+)$")


class KnowledgeChunk:
    __slots__ = ("city", "content_type", "title", "text", "source_file", "keywords")

    def __init__(self, city: str, content_type: str, title: str, text: str, source_file: str):
        self.city = city
        self.content_type = content_type
        self.title = title
        self.text = text
        self.source_file = source_file
        # 标题 + 正文里出现的关键实体，用于加权
        self.keywords = title

    def to_dict(self) -> dict[str, Any]:
        return {
            "city": self.city,
            "contentType": self.content_type,
            "title": self.title,
            "text": self.text,
            "source": self.source_file,
        }


class KnowledgeBase:
    def __init__(self, data_dir: Path = DEFAULT_DATA_DIR):
        self.data_dir = data_dir
        self.chunks: list[KnowledgeChunk] = []
        self.cities: set[str] = set()
        self.load_errors: list[str] = []
        self._load()

    def _load(self) -> None:
        if not self.data_dir.exists():
            self.load_errors.append(f"数据目录不存在: {self.data_dir}")
            return
        for type_dir, content_type in TYPE_DIRS.items():
            folder = self.data_dir / type_dir
            if not folder.exists():
                self.load_errors.append(f"类型目录不存在: {folder}")
                continue
            for md in sorted(folder.glob("*.md")):
                try:
                    self._parse_file(md, content_type)
                except Exception as exc:  # 单文件失败隔离
                    self.load_errors.append(f"{md.name}: {type(exc).__name__} {exc}")

    def _parse_file(self, path: Path, content_type: str) -> None:
        raw = path.read_text(encoding="utf-8", errors="replace")
        # 城市名以文件名为准（元数据里的"云南多城市""杭州、乌镇"是描述，不适合做检索键）
        city = path.stem.replace("景点推荐", "").replace("线路推荐", "").replace("住宿推荐", "")
        city = city.replace("美食推荐", "").replace("交通指南", "").strip() or path.stem

        meta: dict[str, str] = {}
        sections: list[tuple[str, list[str]]] = []
        cur_title = "概览"
        cur_lines: list[str] = []

        for line in raw.splitlines():
            m_head = _HEAD_RE.match(line)
            if m_head:
                # 保存上一节
                if cur_lines:
                    sections.append((cur_title, cur_lines))
                    cur_lines = []
                cur_title = m_head.group(2).strip()
                if cur_title == "元数据":
                    cur_title = "__meta__"
                continue
            m_meta = _META_RE.match(line)
            if cur_title == "__meta__" and m_meta:
                meta[m_meta.group(1).strip()] = m_meta.group(2).strip()
                continue
            if line.strip():
                cur_lines.append(line.strip())
        if cur_lines:
            sections.append((cur_title, cur_lines))

        # 城市/目的地元数据仅作描述，不覆盖文件名推导出的检索键（避免"云南多城市"等无法匹配）

        self.cities.add(city)
        for title, lines in sections:
            if title == "__meta__" or not lines:
                continue
            text = "\n".join(lines)
            if len(text) < 20:  # 太短的片段跳过
                continue
            self.chunks.append(
                KnowledgeChunk(city=city, content_type=content_type, title=title, text=text, source_file=path.name)
            )

    # ---------- 检索 ----------
    @staticmethod
    def _tokens(query: str) -> list[str]:
        """从查询里抽取中文词片段（含 bigram 提升召回）。"""
        parts = re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]{2,}", query)
        toks: list[str] = []
        for p in parts:
            toks.append(p)
            if len(p) > 2:
                for i in range(len(p) - 1):
                    toks.append(p[i : i + 2])
        return list(dict.fromkeys(toks))

    def search(
        self,
        query: str = "",
        city: str | None = None,
        content_type: str | None = None,
        top_k: int = 4,
    ) -> list[dict[str, Any]]:
        if not self.chunks:
            return []
        tokens = self._tokens(query) if query else []
        scored: list[tuple[float, KnowledgeChunk]] = []
        for c in self.chunks:
            if city and city not in c.city and c.city not in city:
                continue
            if content_type and c.content_type != content_type:
                continue
            score = 0.0
            hay_title = c.title
            hay_text = c.text
            for t in tokens:
                if t in hay_title:
                    score += 3.0
                if t in hay_text:
                    score += 1.0 + min(hay_text.count(t), 5) * 0.2
            if not tokens:  # 无查询词时，按城市/类型过滤后取前几条
                score = 1.0
            if score > 0:
                scored.append((score, c))
        scored.sort(key=lambda x: -x[0])
        # 截取正文，避免过长
        out: list[dict[str, Any]] = []
        for _, c in scored[:top_k]:
            d = c.to_dict()
            if len(d["text"]) > 420:
                d["text"] = d["text"][:420] + "…"
            out.append(d)
        return out

    def stats(self) -> dict[str, Any]:
        by_type: dict[str, int] = {}
        for c in self.chunks:
            by_type[c.content_type] = by_type.get(c.content_type, 0) + 1
        return {
            "chunks": len(self.chunks),
            "cities": sorted(self.cities),
            "byType": by_type,
            "loadErrors": self.load_errors,
        }


# 单例：服务启动时构造一次
KB = KnowledgeBase()
