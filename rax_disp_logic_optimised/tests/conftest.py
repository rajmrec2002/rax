import os
import sys

# Make `import rax_disp_logic_optimised` work when running `pytest tests/`
# from inside the package directory or from the repository root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
