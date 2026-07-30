---
type: playbook
status: active
created: 2026-07-29
python_version: "3.11.15"
summary: "Python 3.11 playbook for VaultBot: practical reference covering data structures, control flow, classes, modules, error handling, I/O, and standard library modules most relevant to vaultbot development. Sourced from official Python 3.11 docs."
tags: [python, playbook, reference, software-engineering, python-3.11]
sources:
  - "Python 3.11 Tutorial (docs.python.org/3.11/tutorial/)"
  - "Python 3.11 Standard Library (docs.python.org/3.11/library/)"
depends_on:
  - "[[What-Is-A-Bit]]"
  - "[[History-of-Computation-Before-Digital]]"
---

# Python 3.11 Playbook

> **Python version in vaultbot_venv: 3.11.15** (MSC v.1944, AMD64)
> This playbook covers the Python features most relevant to VaultBot development. For full docs, see the ingested textbook notes in `09-Textbooks/python-*`.

## Quick Reference: Data Structures

### Lists (mutable, ordered)
```python
# Creation
fruits = ['apple', 'banana', 'cherry']
mixed = [1, 'two', 3.0, [4, 5]]

# Common operations
fruits.append('date')           # Add to end
fruits.insert(0, 'apricot')     # Insert at index
fruits.pop()                     # Remove and return last
fruits.remove('banana')         # Remove by value
len(fruits)                      # Length
'apple' in fruits               # Membership test
fruits[1:3]                      # Slice [1, 3)
fruits[::-1]                     # Reverse
fruits.sort()                    # Sort in place
fruits.extend(['elder', 'fig'])  # Extend with another list

# List comprehension
squares = [x**2 for x in range(10)]
evens = [x for x in range(20) if x % 2 == 0]
flat = [item for sublist in nested for item in sublist]
```

### Dictionaries (mutable, key-value)
```python
# Creation
person = {'name': 'Sean', 'age': 30}
person = dict(name='Sean', age=30)

# Operations
person['role'] = 'operator'      # Add/update
del person['age']                # Delete
person.get('email', 'N/A')       # Safe access with default
person.keys()                    # dict_keys view
person.values()                  # dict_values view
person.items()                   # dict_items view
{**dict1, **dict2}              # Merge dicts (3.5+)

# Dict comprehension
{k: v**2 for k, v in items.items() if v > 0}
```

### Sets (mutable, unordered, unique)
```python
# Creation
unique = {1, 2, 3, 3}  # {1, 2, 3}
empty = set()          # NOT {} (that is a dict)

# Operations
a | b    # Union
a & b    # Intersection
a - b    # Difference
a ^ b    # Symmetric difference
a.issubset(b)
a.issuperset(b)
```

### Tuples (immutable, ordered)
```python
point = (3, 4)
single = (42,)  # Single element needs comma
x, y = point    # Unpacking
a, *rest = [1, 2, 3, 4]  # Extended unpacking
```

## Quick Reference: Control Flow

### if / elif / else
```python
if x > 0:
    print("positive")
elif x < 0:
    print("negative")
else:
    print("zero")
```

### for loops
```python
for item in iterable:
    process(item)

for i, item in enumerate(items):
    print(f"{i}: {item}")

for key, value in dict.items():
    process(key, value)

for a, b in zip(list_a, list_b):
    process(a, b)
```

### match statement (Python 3.10+)
```python
match command:
    case "quit":
        exit()
    case "go" direction:
        move(direction)
    case [x, y]:
        process_point(x, y)
    case _:
        print("unknown")
```

### while / break / continue
```python
while condition:
    do_something()
    if done:
        break
    if skip:
        continue
else:
    # Runs if loop completes without break
    print("loop finished normally")
```

## Quick Reference: Functions

```python
# Basic function
def greet(name: str) -> str:
    # Return a greeting
    return f"Hello, {name}"

# Default arguments
def connect(host, port=80, timeout=30):
    pass

# Keyword arguments
connect(host="localhost", timeout=10)

# *args and **kwargs
def flexible(*args, **kwargs):
    print(args)    # tuple of positional args
    print(kwargs)  # dict of keyword args

# Keyword-only arguments (after *)
def safe_function(a, b, *, debug=False):
    pass

# Positional-only arguments (before /)
def internal(x, y, /, z):
    pass  # x, y must be positional

# Lambda expressions
square = lambda x: x ** 2
sorted(items, key=lambda x: x.priority)

# Type hints
def process(data: list[dict[str, int]]) -> dict[str, list[int]]:
    pass
```

## Quick Reference: Classes

```python
class Tool:
    # Base class for vault tools

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self._calls = 0  # private by convention
        self.__locked = False  # Name-mangled private

    def __repr__(self) -> str:
        return f"Tool(name={self.name!r})"

    def __str__(self) -> str:
        return self.name

    @property
    def calls(self) -> int:
        return self._calls

    @classmethod
    def from_config(cls, config: dict) -> 'Tool':
        return cls(config['name'], config['description'])

    @staticmethod
    def validate_name(name: str) -> bool:
        return bool(name) and name.isidentifier()

class CustomTool(Tool):
    # Inherits from Tool

    def __init__(self, name, description, priority: int = 0):
        super().__init__(name, description)
        self.priority = priority

    def run(self, args: dict) -> dict:
        self._calls += 1
        return {"status": "ok"}

# Data classes (simpler class definition)
from dataclasses import dataclass

@dataclass
class ResearchResult:
    topic: str
    sources: list
    summary: str
    word_count: int = 0
```

## Quick Reference: Error Handling

```python
# Basic try/except
try:
    result = risky_operation()
except ValueError as e:
    print(f"Value error: {e}")
except (TypeError, KeyError) as e:
    print(f"Type or key error: {e}")
except Exception as e:
    print(f"Unexpected: {e}")
    raise  # Re-raise
else:
    # Runs if no exception
    print("Success")
finally:
    # Always runs
    cleanup()

# Custom exceptions
class VaultError(Exception):
    # Base exception for vault operations
    pass

class NoteNotFoundError(VaultError):
    def __init__(self, path: str):
        self.path = path
        super().__init__(f"Note not found: {path}")

# Exception chaining
try:
    process()
except SomeError as e:
    raise VaultError("Processing failed") from e
```

## Quick Reference: Modules and Imports

```python
# Standard imports
import os
import json
from pathlib import Path
from typing import Optional, Union, Any

# Relative imports (within a package)
from . import sibling_module
from .. import parent_module

# __name__ == "__main__" guard
if __name__ == "__main__":
    main()

# __all__ controls what from module import * exports
__all__ = ['public_function', 'PublicClass']

# sys.path manipulation
import sys
sys.path.insert(0, '/custom/path')
```

## Quick Reference: File I/O

```python
# Reading files
with open('note.md', 'r', encoding='utf-8') as f:
    content = f.read()        # Read all
    lines = f.readlines()     # Read lines
    for line in f:            # Iterate (memory efficient)
        process(line)

# Writing files
with open('output.md', 'w', encoding='utf-8') as f:
    f.write(content)

# JSON
import json
data = json.loads(json_string)           # Parse JSON
json_string = json.dumps(data, indent=2) # Serialize
with open('data.json', 'w') as f:
    json.dump(data, f, indent=2)

# pathlib (preferred over os.path)
from pathlib import Path
vault = Path('C:/Users/skell/Desktop/Vault2')
notes = list(vault.glob('**/*.md'))       # All markdown files
note = vault / '07-Research' / 'note.md' # Path joining
note.read_text(encoding='utf-8')         # Read file
note.write_text(content, encoding='utf-8')  # Write file
note.exists()                             # Check existence
note.stat().st_size                       # File size
```

## Quick Reference: Standard Library (Most Used in VaultBot)

### os / pathlib - File system
```python
import os
from pathlib import Path

os.path.join('dir', 'file.md')  # OS-aware path joining
os.listdir('.')                 # List directory
os.makedirs('dir/sub', exist_ok=True)  # Create dirs

Path.home()                     # User home directory
Path.cwd()                      # Current working directory
```

### json - Serialization
```python
import json
json.dumps({"key": "value"})    # Dict to JSON string
json.loads('{"key": "value"}')  # JSON string to dict
```

### subprocess - Running commands
```python
import subprocess
result = subprocess.run(['python', '--version'],
                       capture_output=True, text=True)
print(result.stdout)
print(result.returncode)
```

### re - Regular expressions
```python
import re
pattern = r"\[\[(.+?)\]\]"  # Match [[wikilinks]]
matches = re.findall(pattern, text)
cleaned = re.sub(pattern, r"\1", text)
```

### typing - Type hints
```python
from typing import Optional, Union, Any, Callable

def search(query: str, k: int = 5) -> list[dict[str, Any]]:
    pass
```

### dataclasses - Boilerplate-free classes
```python
from dataclasses import dataclass, field

@dataclass
class Gap:
    type: str
    target: str
    priority: float
    sources: list[str] = field(default_factory=list)
```

### enum - Named constants
```python
from enum import Enum, auto

class Status(Enum):
    ACTIVE = auto()
    COMPLETE = auto()
    FAILED = auto()
```

### logging - Structured logging
```python
import logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.info("Process started")
logger.error("Failed: %s", error)
```

### collections - Specialized containers
```python
from collections import defaultdict, Counter, deque

counts = Counter(items)           # Count occurrences
grouped = defaultdict(list)        # Auto-init dict values
queue = deque(maxlen=100)          # Bounded queue
```

### functools - Higher-order functions
```python
from functools import lru_cache, partial, reduce

@lru_cache(maxsize=128)
def expensive(x):
    return compute(x)

bind = partial(func, arg1=fixed_value)
```

### itertools - Iterator tools
```python
from itertools import chain, combinations, groupby, product

# Flatten
flat = list(chain.from_iterable(nested))

# All pairs
for a, b in combinations(items, 2):
    pass

# Cartesian product
for x, y in product(range_x, range_y):
    pass
```

## Python 3.11 Specific Features

### Exception Groups and except*
```python
try:
    raise ExceptionGroup("multiple", [ValueError(1), TypeError(2)])
except* ValueError:
    pass
except* TypeError:
    pass
```

### Self type (PEP 673)
```python
from typing import Self

class Tool:
    def clone(self) -> Self:
        return type(self)(self.name)
```

### tomllib (built-in TOML parsing)
```python
import tomllib
with open('pyproject.toml', 'rb') as f:
    config = tomllib.load(f)
```

## VaultBot Development Patterns

### Pattern: Tool Creation
```python
def run(args: dict) -> dict:
    # Tool entry point. Always returns a dict.
    try:
        # Validate inputs
        required = args.get('required_param')
        if not required:
            return {"status": "error", "message": "Missing required_param"}

        # Do work
        result = process(required)

        return {"status": "ok", "result": result}
    except Exception as e:
        return {"status": "error", "message": str(e)}
```

### Pattern: Safe File Operations
```python
from pathlib import Path
import tempfile, shutil

def safe_write_file(path: Path, content: str) -> bool:
    # Write file atomically: write to temp, then rename.
    backup = path.with_suffix('.bak')
    if path.exists():
        shutil.copy2(path, backup)

    try:
        with tempfile.NamedTemporaryFile(
            mode='w', encoding='utf-8',
            dir=path.parent, delete=False
        ) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)

        tmp_path.replace(path)
        return True
    except Exception:
        if backup.exists():
            shutil.copy2(backup, path)
        return False
```

### Pattern: Markdown Note Operations
```python
import re
from pathlib import Path

def extract_wikilinks(content: str) -> list[str]:
    # Extract all [[wikilink]] targets from markdown.
    pattern = r"\[\[([^\]]+)\]\]"
    return re.findall(pattern, content)
```

## Related

- [[Research-Roadmap]] - Phase 3, topic 5
- [[What-Is-A-Bit]] - the bit as Python foundation
- [[History-of-Computation-Before-Digital]] - Python in the lineage of computing
- [[Knowledge-Behavior-Meaning-in-Bits]] - Python programs as behavior in bits
- Python 3.11 textbook notes - `09-Textbooks/python-*` (139+ ingested sections)
## Python Textbook References

This playbook summarizes Python 3.11 features. For deeper coverage, see the ingested textbook notes:

- [[python-4more-control-flow-tools]] — if/for/while, break/continue, match statements
- [[python-5data-structures]] — lists, dicts, sets, tuples, comprehensions
- [[python-6modules]] — module system, imports, packages
- [[python-7input-and-output]] — file I/O, string formatting
- [[python-8errors-and-exceptions]] — exception handling, try/except/finally
- [[python-9classes]] — classes, inheritance, dataclasses
- [[python-487documentation-strings]] — docstrings
- [[python-1011quality-control]] — testing and quality control
