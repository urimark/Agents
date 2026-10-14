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

### Modes
1. **Single Run Mode**: Analyzes an ATPG run, identifies coverage holes, and produces an interactive coverage dashboard and AU CSV breakdown.
2. **Two-Run Comparison Mode**: Compares 2 ATPG runs (e.g., TTLC vs RZL, Stuck-At vs At-Speed), analyzes cross-run divergence, and produces **3 distinct reports**:
   - **Report 1**: Standalone coverage report for Run 1 (`atpg_coverage_report_run1.html`)
   - **Report 2**: Standalone coverage report for Run 2 (`atpg_coverage_report_run2.html`)
   - **Report 3**: Cross-Run Comparison & Improvement Advisory report (`atpg_coverage_comparison_report.html`) + deltas CSV (`au_comparison_deltas.csv`).

### CLI Usage

#### 1. Single Run:
```bash
python3 atpg_coverage_reporter.py --run-dir <atpg_run_directory> [options]
```

#### 2. Compare 2 Runs (Produces 3 Reports):
```bash
python3 atpg_coverage_reporter.py \
  --run-dir <run_dir_1> \
  --compare-run-dir <run_dir_2> \
  --label1 "<Label1>" \
  --label2 "<Label2>" \
  [options]
```

#### Options:
- `--run-dir <dir>`: Path to Run 1 ATPG folder (required).
- `--compare-run-dir <dir>` / `--run-dir2 <dir>`: Path to Run 2 ATPG folder for comparison.
- `--label1 <str>` / `--label2 <str>`: Display names for Run 1 and Run 2.
- `--recipe1 <str>` / `--recipe2 <str>`: Explicit recipe names (auto-detected if omitted).
- `--output-dir <dir>`: Output directory for reports and CSVs.
- `--skip-tessent`: Skip Tessent and reuse existing `instance_au_breakdown*.csv` files.
- `--top-n <int>`: Number of top coverage holes to display (default: 30).
- `--ww [TAG]`: Save reports by WorkWeek and day folder (e.g., `results/WW42.Wed/`). Automatically computes current WW if passed without value, or accepts custom tag (e.g. `--ww WW42.Wed`).
- `--save-ww`: Convenience flag to enable saving under WorkWeek folder.
- `--load-git` / `--git-push`: Automatically copy generated reports into the Git `results/<WW>/` directory, commit, and push to remote origin.
- `--git-repo <path>`: Local Git repository path (defaults to current repo).

#### Examples with WorkWeek and Git Loading:
```bash
# Save to results/WW42.Wed/ and load to Git:
python3 atpg_coverage_reporter.py --run-dir /path/to/run/ --ww --load-git

# Compare 2 runs, save to results/WW42.Wed/, and push all 3 reports to Git:
python3 atpg_coverage_reporter.py \
  --run-dir /path/to/run1/ \
  --compare-run-dir /path/to/run2/ \
  --label1 "TTLC" --label2 "RZL" \
  --ww --load-git
```

### Chat Agent Usage
In VS Code Copilot Chat, invoke:
```
# Single run:
@ATPG Coverage Report /path/to/atpg_run_dir/

# Single run with WorkWeek git upload:
@ATPG Coverage Report /path/to/atpg_run_dir/ and save by WW to git

# Compare 2 runs and load to git:
@ATPG Coverage Report compare /path/to/run1/ with /path/to/run2/ --ww --load-git
```

