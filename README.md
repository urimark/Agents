# Agents
VSCode Agents repository

## ATPG Coverage & AU Breakdown Agent

An intelligent agent and automation engine for Siemens Tessent ATPG test coverage extraction, AU breakdown generation, hierarchical coverage hole identification, and interactive HTML reporting.

### Components
- **Agent Definition**: `.github/agents/atpg-coverage-report.agent.md`
- **Automation Engine**: `atpg_coverage_reporter.py`

### Features
- **Auto-discovery of ATPG artifacts**: Automatically locates ICL, flat model (`*internal.flat.gz`), fault lists, and disposition files from standard Sage ATPG run folders.
- **Tessent Batch Extraction**: Drives Siemens Tessent Shell (`report_statistics -Hierarchy -CSV`) to extract hierarchical AU fault distributions without manual TCL scripting.
- **Hierarchical Coverage Hole Detection**: Ranks sub-blocks, memory wrappers (`ccf_rw_array_wrapper`), clock/reset controllers (`cdu_`), and interfaces by untestable fault count.
- **Actionable Engineering Next Steps**: Prioritizes concrete DFT fixes (RAM blackboxing, clock/reset gating bypasses, DRC fixes, disposition waivers) to maximize test coverage.
- **Interactive HTML Dashboard**: Produces executive KPI cards, search/filterable coverage hole tables, partition health status, and direct recommendations.

### CLI Usage
```bash
python3 atpg_coverage_reporter.py --run-dir <atpg_run_directory> [options]
```

#### Options:
- `--run-dir <dir>`: Path to the ATPG run folder (required).
- `--recipe <recipe>`: Target recipe (e.g., `stuckat_edt_min`, `atspeed_edt_min`).
- `--output-dir <dir>`: Directory for generated CSV and HTML reports (defaults to run-dir).
- `--skip-tessent`: Skip Tessent execution and analyze existing `instance_au_breakdown.csv`.
- `--top-n <int>`: Number of top coverage holes to display (default: 30).

### Chat Agent Usage
In VS Code Copilot Chat, invoke:
```
@ATPG Coverage Report /path/to/atpg_run_dir/
```

