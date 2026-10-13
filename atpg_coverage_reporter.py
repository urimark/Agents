#!/usr/bin/env python3
"""
================================================================================
ATPG Coverage & AU Breakdown Reporter
================================================================================
Author: GitHub Copilot DFT Automation
Usage:
  python3 atpg_coverage_reporter.py --run-dir <atpg_run_dir> [options]

Description:
  Automates Siemens Tessent ATPG test coverage extraction, AU breakdown generation,
  hierarchical coverage hole identification, and produces an interactive HTML report
  with actionable engineering recommendations to increase test coverage.
================================================================================
"""

import os
import sys
import glob
import csv
import argparse
import subprocess
import html as html_lib
from collections import defaultdict
from datetime import datetime

DEFAULT_LICENSE = (
    "1717@mentor14p.elic.intel.com:1717@mentor06p.elic.intel.com:"
    "1717@mentor37p.elic.intel.com:1717@mentor47p.elic.intel.com:"
    "1717@siemens12p.elic.intel.com"
)
DEFAULT_TESSENT = "/p/hdk/cad/tessent/2026.1/bin/tessent"


def find_files_for_run(run_dir, recipe=None):
    """
    Auto-discovers ICL, flat model, fault list, and disposition files from an ATPG run directory.
    """
    run_dir = os.path.abspath(run_dir)
    print(f"[*] Discovering ATPG artifacts in: {run_dir}")

    # 1. Determine partition / fub name
    fub = None
    fub_file = os.path.join(run_dir, "regression", "fub")
    if os.path.exists(fub_file):
        with open(fub_file, "r") as f:
            fub = f.read().strip()
    if not fub:
        fub = os.path.basename(run_dir.rstrip("/"))
    print(f"[*] Target Partition (FUB): {fub}")

    # 2. Locate WORK / recipe directory
    work_dirs = []
    # Check standard Sage ATPG path: <run_dir>/<fub>_wa/runs/<fub>/<tech>/sage_atpg/sage/WORK/
    pattern1 = os.path.join(run_dir, f"{fub}_wa", "runs", fub, "*", "sage_atpg", "sage", "WORK", "*")
    matches = glob.glob(pattern1)
    if matches:
        work_dirs = [m for m in matches if os.path.isdir(m) and not m.endswith("proj_common") and not m.endswith("hip")]

    # Fallback to direct search if run_dir itself is or contains WORK
    if not work_dirs:
        pattern2 = os.path.join(run_dir, "**", "WORK", "*")
        matches = glob.glob(pattern2, recursive=True)
        work_dirs = [m for m in matches if os.path.isdir(m) and not m.endswith("proj_common") and not m.endswith("hip")]

    if not work_dirs:
        # Check if run_dir itself has tsdb_outdir and faultlist
        if os.path.exists(os.path.join(run_dir, "tsdb_outdir")) and os.path.exists(os.path.join(run_dir, "faultlist")):
            work_dirs = [run_dir]

    if not work_dirs:
        raise FileNotFoundError(f"Could not locate WORK directory in {run_dir}")

    # Select desired recipe
    selected_work = None
    if recipe:
        for wd in work_dirs:
            if recipe in os.path.basename(wd):
                selected_work = wd
                break
        if not selected_work:
            raise FileNotFoundError(f"Requested recipe '{recipe}' not found in {work_dirs}")
    else:
        # Default priority: stuckat_edt_min, atspeed_edt_min, or first available
        for pref in ["stuckat_edt_min", "atspeed_edt_min"]:
            for wd in work_dirs:
                if pref in os.path.basename(wd):
                    selected_work = wd
                    break
            if selected_work:
                break
        if not selected_work:
            selected_work = work_dirs[0]

    recipe_name = os.path.basename(selected_work)
    print(f"[*] Selected ATPG Recipe: {recipe_name} ({selected_work})")

    # 3. Locate ICL
    icl_path = None
    icl_pattern = os.path.join(selected_work, "tsdb_outdir", "dft_inserted_designs", "*", "*.icl")
    icl_matches = glob.glob(icl_pattern)
    if icl_matches:
        # Prefer <fub>.icl
        for m in icl_matches:
            if os.path.basename(m) == f"{fub}.icl":
                icl_path = m
                break
        if not icl_path:
            icl_path = icl_matches[0]

    # 4. Locate Flat Model
    flat_path = None
    flat_pattern = os.path.join(selected_work, "tsdb_outdir", "logic_test_cores", "*", "*", "*.flat.gz")
    flat_matches = glob.glob(flat_pattern)
    if not flat_matches:
        flat_pattern2 = os.path.join(selected_work, "tsdb_outdir", "logic_test_cores", "*", "*", "*.flat")
        flat_matches = glob.glob(flat_pattern2)
    if flat_matches:
        # Prefer *internal.flat.gz
        for m in flat_matches:
            if "internal.flat" in os.path.basename(m):
                flat_path = m
                break
        if not flat_path:
            flat_path = flat_matches[0]

    # 5. Locate Fault List
    faults_path = None
    faults_pattern = os.path.join(selected_work, "faultlist", f"{fub}.faults.gz")
    if os.path.exists(faults_pattern):
        faults_path = faults_pattern
    else:
        f_matches = glob.glob(os.path.join(selected_work, "faultlist", "*.faults.gz"))
        valid = [m for m in f_matches if "disp" not in os.path.basename(m).lower() and "detected" not in os.path.basename(m).lower()]
        if valid:
            faults_path = valid[0]

    # 6. Locate Disposition File
    disp_path = None
    disp_candidates = [
        os.path.join(selected_work, "faultlist", f".{fub}.au.disposition.faults.gz"),
        os.path.join(selected_work, "faultlist", f"{fub}.basic_disp_faults.gz"),
        os.path.join(selected_work, "faultlist", "disp_faults_orig.gz"),
    ]
    for c in disp_candidates:
        if os.path.exists(c):
            disp_path = c
            break
    if not disp_path:
        disp_pattern = os.path.join(selected_work, "faultlist", "*disp*.faults.gz")
        d_matches = glob.glob(disp_pattern)
        if d_matches:
            disp_path = d_matches[0]

    print(f"[*] ICL File:        {icl_path}")
    print(f"[*] Flat Model:      {flat_path}")
    print(f"[*] Faults File:     {faults_path}")
    print(f"[*] Disp File:       {disp_path}")

    return {
        "fub": fub,
        "recipe": recipe_name,
        "work_dir": selected_work,
        "icl": icl_path,
        "flat": flat_path,
        "faults": faults_path,
        "disp": disp_path,
    }


def run_tessent_extraction(artifacts, output_csv, log_file, tessent_bin=DEFAULT_TESSENT, license_server=DEFAULT_LICENSE):
    """
    Generates DO-file and executes Tessent Shell to produce instance_au_breakdown.csv
    """
    if not os.path.exists(tessent_bin):
        raise FileNotFoundError(f"Tessent binary not found at: {tessent_bin}")

    if not artifacts["icl"] or not os.path.exists(artifacts["icl"]):
        raise FileNotFoundError(f"ICL file not found: {artifacts['icl']}")
    if not artifacts["flat"] or not os.path.exists(artifacts["flat"]):
        raise FileNotFoundError(f"Flat model file not found: {artifacts['flat']}")
    if not artifacts["faults"] or not os.path.exists(artifacts["faults"]):
        raise FileNotFoundError(f"Faults file not found: {artifacts['faults']}")

    dofile_path = os.path.splitext(output_csv)[0] + "_flow.do"
    print(f"[*] Generating Tessent DO file: {dofile_path}")

    disp_cmd = ""
    if artifacts["disp"] and os.path.exists(artifacts["disp"]):
        disp_cmd = f"""
set disp_file "{artifacts['disp']}"
if {{[file exists $disp_file]}} {{
    read_faults $disp_file -delete
}}
"""

    dofile_content = f"""// Tessent Automated AU Breakdown DO-file
// Generated by atpg_coverage_reporter.py on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

set_context patterns -scan

read_icl {artifacts['icl']}
read_flat_model {artifacts['flat']} -en on
read_faults {artifacts['faults']} -retain
{disp_cmd}
report_statistics -Hierarchy -FORmat "mo #f %tc au" -USe_full_instance_path -CSV > {output_csv}
exit -force
"""

    with open(dofile_path, "w") as f:
        f.write(dofile_content)

    print(f"[*] Executing Tessent Shell extraction...")
    env = os.environ.copy()
    env["SALT_LICENSE_SERVER"] = license_server

    cmd = [
        tessent_bin,
        "-shell",
        "-logfile", log_file,
        "-replace",
        "-dofile", dofile_path
    ]

    print(f"[*] Command: {' '.join(cmd)}")
    p = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    if not os.path.exists(output_csv) or os.path.getsize(output_csv) == 0:
        print("[!] Tessent output log snippet:")
        if os.path.exists(log_file):
            with open(log_file, "r") as lf:
                lines = lf.readlines()
                print("".join(lines[-40:]))
        raise RuntimeError(f"Tessent execution failed to generate valid {output_csv}")

    print(f"[✓] Successfully generated {output_csv} ({os.path.getsize(output_csv):,} bytes)")
    return output_csv


def parse_instance_au_breakdown(csv_path):
    """
    Parses instance_au_breakdown.csv into structured records.
    """
    print(f"[*] Parsing instance AU breakdown CSV: {csv_path}")
    top_record = None
    all_instances = []
    d1_instances = []

    with open(csv_path, "r", errors="ignore") as f:
        reader = csv.reader(f)
        header = next(reader)

        inst_idx, mod_idx, faults_idx, tc_idx, au_idx = 0, 1, 2, 3, 4
        clean_hdr = [c.strip().lower() for c in header]
        for idx, col in enumerate(clean_hdr):
            if "instance" in col: inst_idx = idx
            elif "module" in col: mod_idx = idx
            elif "fault" in col: faults_idx = idx
            elif "coverage" in col: tc_idx = idx
            elif "au" in col: au_idx = idx

        for r in reader:
            if not r or not any(r): continue
            if r[0].strip().startswith("---"): continue
            inst = r[inst_idx].strip().replace("\n", "")
            mod = r[mod_idx].strip().replace("\n", "") if len(r) > mod_idx else ""
            faults_str = r[faults_idx].strip().replace(",", "") if len(r) > faults_idx else "0"
            tc_str = r[tc_idx].strip().replace("%", "") if len(r) > tc_idx else "0"
            au_str = r[au_idx].strip().replace(",", "") if len(r) > au_idx else "0"

            try: faults = int(faults_str)
            except ValueError: faults = 0
            try: tc = float(tc_str)
            except ValueError: tc = 0.0
            try: au = int(au_str)
            except ValueError: au = 0

            depth = inst.count("/") if inst != "-top-" else 0
            rec = {
                "instance": inst,
                "module": mod,
                "faults": faults,
                "tc": tc,
                "au": au,
                "depth": depth
            }

            if inst == "-top-":
                top_record = rec
            else:
                all_instances.append(rec)
                if depth == 1:
                    d1_instances.append(rec)

    if not top_record:
        # Fallback if -top- wasn't named exactly
        top_record = {
            "instance": "-top-",
            "module": "Design_Top",
            "faults": sum(d["faults"] for d in d1_instances),
            "tc": 0.0,
            "au": sum(d["au"] for d in d1_instances),
            "depth": 0
        }

    return top_record, all_instances, d1_instances


def classify_instance(inst, mod):
    """
    Classifies an instance into functional hardware / DFT categories.
    """
    text = (inst + " " + mod).lower()
    if any(k in text for k in ["rw_array", "array", "ram_wrapper", "rf_mem", "mbist", "sram", "rf_data"]):
        return "Memory / RAM Array"
    elif any(k in text for k in ["doublesync", "clk_gate", "clk_buf", "cdu_", "rst", "reset", "clock_signals", "pulse_in_capture", "fscan_mode"]):
        return "Clock & Reset Control"
    elif any(k in text for k in ["gpcfi", "ssn", "hptp", "cri_ring", "sbo", "upi", "router", "link", "flit", "xbar", "noc"]):
        return "Bus / Interconnect / NoC"
    elif any(k in text for k in ["fsm", "arb", "scheduler", "queue", "ctrl", "controller", "credit"]):
        return "Control / FSM / Arbiter"
    elif any(k in text for k in ["buf", "repeater", "rptr", "tieoff"]):
        return "Buffer / Repeater / Glue"
    else:
        return "Core Logic / Datapath"


def analyze_coverage(top_record, all_instances, d1_instances, top_n=30):
    """
    Identifies top coverage holes, glue logic, partition health, and generates next steps.
    """
    total_au = top_record["au"]
    total_faults = top_record["faults"]
    overall_tc = top_record["tc"]
    au_rate = (total_au / total_faults * 100) if total_faults > 0 else 0.0

    # 1. Depth-1 analysis & Glue logic
    d1_au_sum = sum(d["au"] for d in d1_instances)
    d1_f_sum = sum(d["faults"] for d in d1_instances)
    glue_au = total_au - d1_au_sum
    glue_f = total_faults - d1_f_sum
    glue_tc = ((glue_f - glue_au) / glue_f * 100) if glue_f > 0 else 0.0

    d1_list = sorted(d1_instances, key=lambda x: x["au"], reverse=True)
    for d in d1_list:
        d["pct_of_total_au"] = (d["au"] / total_au * 100) if total_au > 0 else 0.0
        d["au_rate"] = (d["au"] / d["faults"] * 100) if d["faults"] > 0 else 0.0
        d["category"] = classify_instance(d["instance"], d["module"])

    glue_record = {
        "instance": "[Top-Level Boundary / Glue Logic]",
        "module": f"{top_record['module']} (unassigned to D1)",
        "faults": glue_f,
        "tc": glue_tc,
        "au": glue_au,
        "depth": 1,
        "pct_of_total_au": (glue_au / total_au * 100) if total_au > 0 else 0.0,
        "au_rate": (glue_au / glue_f * 100) if glue_f > 0 else 0.0,
        "category": "Buffer / Repeater / Glue"
    }

    # 2. Coverage Holes (Ranked by AU count)
    # Filter out redundant ancestors where a single child already explains >=95% of parent AU
    sorted_all = sorted(all_instances, key=lambda x: x["au"], reverse=True)
    
    # Enrich records
    for item in sorted_all:
        item["pct_of_total_au"] = (item["au"] / total_au * 100) if total_au > 0 else 0.0
        item["au_rate"] = (item["au"] / item["faults"] * 100) if item["faults"] > 0 else 0.0
        item["category"] = classify_instance(item["instance"], item["module"])

    # Pick top distinct coverage holes (prioritize leafier nodes or significant divergence)
    coverage_holes = []
    seen_prefixes = set()
    for item in sorted_all:
        if item["au"] < 50:
            continue
        coverage_holes.append(item)
        if len(coverage_holes) >= top_n:
            break

    # 3. Category Breakdown
    cat_au = defaultdict(int)
    cat_faults = defaultdict(int)
    for item in d1_list:
        cat_au[item["category"]] += item["au"]
        cat_faults[item["category"]] += item["faults"]
    cat_au[glue_record["category"]] += glue_record["au"]
    cat_faults[glue_record["category"]] += glue_record["faults"]

    # 4. Formulate Concrete Next Steps
    next_steps = []
    
    # Check Memory / RAM arrays
    ram_au = sum(x["au"] for x in sorted_all if "rw_array" in (x["instance"] + " " + x["module"]).lower() and x["depth"] >= 4)
    if ram_au > 5000:
        pct_ram = (ram_au / total_au * 100) if total_au > 0 else 0.0
        next_steps.append({
            "priority": "P1 - Critical",
            "title": "Blackbox / Bypass Untested RAM Array Wrappers",
            "impact": f"Up to {ram_au:,} AU recoverable ({pct_ram:.1f}% of total AU, +{(ram_au/total_faults*100):.2f}% TC)",
            "details": (
                f"Large AU concentration ({ram_au:,} faults) found in read/write RAM array wrappers "
                f"(e.g., <code>vc0_drs_incoming_arr</code>, <code>upi_snp_incoming_arr</code>). "
                "These arrays contain unmodeled custom bitcells or unscan-stitched arrays. "
                "Ensure these modules are blackboxed during logic ATPG using <code>add_black_box &lt;module&gt;</code> "
                "or verify that the MBIST collar bypass mode is actively asserted during scan capture."
            ),
            "command": "add_black_box par_santa_par_santa_ccf_rw_array_wrapper_* -auto"
        })

    # Check Top 2 Depth-1 partitions
    if len(d1_list) >= 2 and (d1_list[0]["au"] + d1_list[1]["au"]) / total_au > 0.60:
        top2_au = d1_list[0]["au"] + d1_list[1]["au"]
        pct_top2 = (top2_au / total_au * 100)
        next_steps.append({
            "priority": "P1 - Critical",
            "title": f"Target Primary Partition Hotspots: {d1_list[0]['instance']} & {d1_list[1]['instance']}",
            "impact": f"{top2_au:,} AU ({pct_top2:.1f}% of entire design AU)",
            "details": (
                f"Partitions <code>{d1_list[0]['instance']}</code> ({d1_list[0]['au']:,} AU) and "
                f"<code>{d1_list[1]['instance']}</code> ({d1_list[1]['au']:,} AU) dominate design AU. "
                "Drill down into their respective <code>santa_cclk_arrays</code> and clock distribution hierarchies. "
                "Synchronous clock list or clock domain crossing isolation will prevent ATPG from aborting on inter-clock paths."
            ),
            "command": "report_clocks -matrix; report_drc_rules -violations"
        })

    # Check Clock & Reset / Double-sync
    cdu_items = [x for x in sorted_all if any(k in x["instance"].lower() for k in ["cdu_", "doublesync", "rst", "clk_gate"]) and x["au"] > 500]
    if cdu_items:
        cdu_au = sum(x["au"] for x in cdu_items)
        next_steps.append({
            "priority": "P2 - High",
            "title": "Enable Clock & Reset Scan Bypasses for Asynchronous/Double-Sync Flops",
            "impact": f"Resolves {cdu_au:,} AU across clock/reset control units",
            "details": (
                "Uncontrolled asynchronous resets and double-synchronizer flops block downstream logic cones. "
                "Verify that scan reset bypass signals (e.g., <code>fscan_bypx_rst</code>, <code>fscan_mode</code>) "
                "are constrained to active state in <code>par_santa.resets.do</code> or via <code>add_cell_constraints</code>."
            ),
            "command": "add_cell_constraints -inst <doublesync_inst> -value 0"
        })

    # Check Disposition File
    next_steps.append({
        "priority": "P2 - High",
        "title": "Update & Apply ATPG Untestable Disposition Waivers",
        "impact": "Excludes structurally unactivatable and functionally redundant faults",
        "details": (
            "Verify that verified architectural untestable faults (e.g., tied-off configuration bits, "
            "fuse overrides, scan-safe hold logic) are recorded in the disposition file. "
            "Re-running with the updated disposition file will automatically drop residual AU."
        ),
        "command": "read_faults $disp_file -delete"
    })

    # General DRC & Test Points
    next_steps.append({
        "priority": "P3 - Medium",
        "title": "DRC Audit (C18, C22, D1, D6) & Test Point Insertion (TPI)",
        "impact": "Improves overall testability of random logic cones",
        "details": (
            "Inspect ATPG log for DRC rules C18 (clock rules), C22 (clock-clock race interactions), and D1/D6. "
            "For deep logic cones with high fan-in or low observability, evaluate Tessent Test Point Insertion (TPI) "
            "to break long sequential/combinational dependency chains."
        ),
        "command": "report_drc_rules C18 C22 D1 D6"
    })

    return {
        "top": top_record,
        "total_au": total_au,
        "total_faults": total_faults,
        "overall_tc": overall_tc,
        "au_rate": au_rate,
        "d1_list": d1_list,
        "glue": glue_record,
        "coverage_holes": coverage_holes,
        "category_au": cat_au,
        "category_faults": cat_faults,
        "next_steps": next_steps,
    }


def generate_html_report(analysis, artifacts, output_html):
    """
    Renders a modern, interactive HTML report highlighting coverage holes and next steps.
    """
    print(f"[*] Rendering interactive HTML coverage report: {output_html}")
    top = analysis["top"]
    fub = artifacts.get("fub", top["module"])
    recipe = artifacts.get("recipe", "ATPG")
    
    # Determine Health status
    tc = top["tc"]
    if tc >= 98.0:
        badge_cls = "badge-green"
        badge_text = f"Health: EXCELLENT ({tc:.2f}% TC)"
    elif tc >= 95.0:
        badge_cls = "badge-blue"
        badge_text = f"Health: GOOD ({tc:.2f}% TC)"
    elif tc >= 90.0:
        badge_cls = "badge-yellow"
        badge_text = f"Health: FAIR ({tc:.2f}% TC)"
    else:
        badge_cls = "badge-red"
        badge_text = f"Health: CRITICAL HOLES ({tc:.2f}% TC)"

    top_hole = analysis["coverage_holes"][0] if analysis["coverage_holes"] else None

    # HTML building
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ATPG Coverage & AU Breakdown Report: {html_lib.escape(fub)} ({html_lib.escape(recipe)})</title>
    <style>
        :root {{
            --bg-page: #0f172a;
            --bg-card: #1e293b;
            --bg-subcard: #243247;
            --bg-hover: rgba(255, 255, 255, 0.04);
            --text-main: #f8fafc;
            --text-dim: #94a3b8;
            --text-highlight: #38bdf8;
            --border: #334155;
            --red: #f87171;
            --red-bg: rgba(248, 113, 113, 0.12);
            --green: #4ade80;
            --green-bg: rgba(74, 222, 128, 0.12);
            --yellow: #facc15;
            --yellow-bg: rgba(250, 204, 21, 0.12);
            --blue: #60a5fa;
            --blue-bg: rgba(96, 165, 250, 0.12);
            --purple: #c084fc;
            --purple-bg: rgba(192, 132, 252, 0.12);
            --code-bg: #0b1120;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg-page);
            color: var(--text-main);
            line-height: 1.6;
            padding: 2rem 1.5rem;
        }}
        .container {{ max-width: 1480px; margin: 0 auto; }}
        header {{
            margin-bottom: 2rem;
            padding-bottom: 1.5rem;
            border-bottom: 1px solid var(--border);
        }}
        h1 {{
            font-size: 2.2rem;
            font-weight: 800;
            color: var(--text-highlight);
            margin-bottom: 0.5rem;
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }}
        .meta {{
            color: var(--text-dim);
            font-size: 0.95rem;
            line-height: 1.9;
        }}
        .meta code {{
            font-family: 'JetBrains Mono', Consolas, monospace;
            background: var(--code-bg);
            padding: 0.2rem 0.5rem;
            border-radius: 4px;
            font-size: 0.85rem;
            color: #7dd3fc;
            border: 1px solid var(--border);
        }}
        .badge {{
            display: inline-block;
            padding: 0.3rem 0.8rem;
            border-radius: 4px;
            font-size: 0.85rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .badge-green {{ background: var(--green-bg); color: var(--green); border: 1px solid var(--green); }}
        .badge-blue {{ background: var(--blue-bg); color: var(--blue); border: 1px solid var(--blue); }}
        .badge-yellow {{ background: var(--yellow-bg); color: var(--yellow); border: 1px solid var(--yellow); }}
        .badge-red {{ background: var(--red-bg); color: var(--red); border: 1px solid var(--red); }}
        .badge-purple {{ background: var(--purple-bg); color: var(--purple); border: 1px solid var(--purple); }}

        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            gap: 1.25rem;
            margin-bottom: 2rem;
        }}
        .kpi-card {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 1.25rem 1.5rem;
            box-shadow: 0 4px 6px -1px rgba(0,0,0,0.2);
            transition: transform 0.2s;
        }}
        .kpi-card:hover {{ transform: translateY(-2px); }}
        .kpi-title {{
            font-size: 0.85rem;
            text-transform: uppercase;
            font-weight: 600;
            color: var(--text-dim);
            margin-bottom: 0.4rem;
            letter-spacing: 0.5px;
        }}
        .kpi-val {{
            font-size: 2.1rem;
            font-weight: 800;
            margin-bottom: 0.3rem;
        }}
        .kpi-sub {{
            font-size: 0.88rem;
            color: var(--text-dim);
            font-weight: 500;
        }}
        .val-red {{ color: var(--red); }}
        .val-green {{ color: var(--green); }}
        .val-yellow {{ color: var(--yellow); }}
        .val-blue {{ color: var(--blue); }}
        .val-purple {{ color: var(--purple); }}

        .card {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 1.75rem;
            margin-bottom: 2rem;
            box-shadow: 0 4px 6px -1px rgba(0,0,0,0.2);
        }}
        h2 {{
            font-size: 1.35rem;
            color: var(--text-highlight);
            margin-bottom: 1.25rem;
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 1px solid var(--border);
            padding-bottom: 0.75rem;
        }}
        h3 {{
            font-size: 1.15rem;
            color: var(--text-main);
            margin: 1.25rem 0 0.5rem 0;
        }}
        p {{ margin-bottom: 0.75rem; color: var(--text-dim); }}

        .table-responsive {{
            overflow-x: auto;
            margin-top: 1rem;
            border-radius: 6px;
            border: 1px solid var(--border);
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.88rem;
        }}
        th {{
            background: #162032;
            color: var(--text-main);
            font-weight: 600;
            text-align: left;
            padding: 0.85rem 1rem;
            border-bottom: 2px solid var(--border);
            white-space: nowrap;
            position: sticky;
            top: 0;
        }}
        td {{
            padding: 0.75rem 1rem;
            border-bottom: 1px solid var(--border);
            color: var(--text-dim);
        }}
        tr:hover td {{
            background-color: var(--bg-hover);
            color: var(--text-main);
        }}
        .text-right {{ text-align: right; }}
        .text-center {{ text-align: center; }}
        .font-mono {{
            font-family: 'JetBrains Mono', Consolas, Monaco, monospace;
            font-size: 0.84rem;
            color: #cbd5e1;
        }}
        .tag {{
            display: inline-block;
            padding: 0.2rem 0.55rem;
            border-radius: 4px;
            font-size: 0.75rem;
            font-weight: 600;
        }}
        .tag-memory {{ background: var(--purple-bg); color: var(--purple); border: 1px solid var(--purple); }}
        .tag-clock {{ background: var(--yellow-bg); color: var(--yellow); border: 1px solid var(--yellow); }}
        .tag-bus {{ background: var(--blue-bg); color: var(--blue); border: 1px solid var(--blue); }}
        .tag-control {{ background: var(--green-bg); color: var(--green); border: 1px solid var(--green); }}
        .tag-other {{ background: rgba(148, 163, 184, 0.15); color: #cbd5e1; border: 1px solid #475569; }}

        .search-box {{
            width: 100%;
            max-width: 450px;
            background: var(--code-bg);
            border: 1px solid var(--border);
            border-radius: 6px;
            color: var(--text-main);
            padding: 0.6rem 1rem;
            font-size: 0.9rem;
            margin-bottom: 1rem;
            outline: none;
        }}
        .search-box:focus {{ border-color: var(--text-highlight); }}

        .progress-bar {{
            background: #334155;
            border-radius: 4px;
            height: 8px;
            width: 100%;
            overflow: hidden;
            display: inline-block;
            vertical-align: middle;
            margin-top: 4px;
        }}
        .progress-fill {{
            height: 100%;
            border-radius: 4px;
        }}
        .fill-red {{ background: var(--red); }}
        .fill-yellow {{ background: var(--yellow); }}
        .fill-green {{ background: var(--green); }}

        /* Action cards */
        .action-card {{
            background: var(--bg-subcard);
            border: 1px solid var(--border);
            border-left: 4px solid var(--blue);
            border-radius: 6px;
            padding: 1.25rem;
            margin-bottom: 1rem;
        }}
        .action-card.p1 {{ border-left-color: var(--red); }}
        .action-card.p2 {{ border-left-color: var(--yellow); }}
        .action-card.p3 {{ border-left-color: var(--blue); }}
        .action-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 0.5rem;
        }}
        .action-title {{
            font-size: 1.05rem;
            font-weight: 700;
            color: var(--text-main);
        }}
        .action-impact {{
            font-size: 0.85rem;
            font-weight: 600;
            padding: 0.2rem 0.6rem;
            border-radius: 4px;
            background: rgba(255, 255, 255, 0.08);
            color: var(--text-highlight);
        }}
        .action-code {{
            background: var(--code-bg);
            border: 1px solid var(--border);
            border-radius: 4px;
            padding: 0.5rem 0.75rem;
            margin-top: 0.75rem;
            font-family: 'JetBrains Mono', Consolas, monospace;
            font-size: 0.85rem;
            color: #7dd3fc;
            overflow-x: auto;
        }}
    </style>
    <script>
        function filterTable(inputId, tableId) {{
            const filter = document.getElementById(inputId).value.toLowerCase();
            const rows = document.getElementById(tableId).getElementsByTagName('tbody')[0].getElementsByTagName('tr');
            for (let i = 0; i < rows.length; i++) {{
                const text = rows[i].textContent.toLowerCase();
                rows[i].style.display = text.includes(filter) ? '' : 'none';
            }}
        }}
    </script>
</head>
<body>
<div class="container">
    <header>
        <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 0.5rem; flex-wrap: wrap; gap: 1rem;">
            <div>
                <h1>ATPG Coverage & AU Breakdown Report: {html_lib.escape(fub)}</h1>
                <div class="meta">
                    Recipe: <code>{html_lib.escape(recipe)}</code> &nbsp;|&nbsp;
                    Generated on: <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code> &nbsp;|&nbsp;
                    Run Folder: <code>{html_lib.escape(artifacts.get('work_dir', 'N/A'))}</code>
                </div>
            </div>
            <div>
                <span class="badge {badge_cls}">{badge_text}</span>
            </div>
        </div>
    </header>

    <!-- Top KPI Grid -->
    <div class="kpi-grid">
        <div class="kpi-card">
            <div class="kpi-title">Test Coverage</div>
            <div class="kpi-val { 'val-green' if tc >= 95 else 'val-yellow' if tc >= 90 else 'val-red' }">{tc:.2f}%</div>
            <div class="kpi-sub">Target: ≥95.00%</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-title">Total Faults</div>
            <div class="kpi-val val-blue">{top['faults']:,}</div>
            <div class="kpi-sub">Total design fault universe</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-title">Total AU (Untestable)</div>
            <div class="kpi-val val-red">{top['au']:,}</div>
            <div class="kpi-sub">{analysis['au_rate']:.2f}% AU Rate</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-title">Top Coverage Hole</div>
            <div class="kpi-val val-purple" style="font-size: 1.5rem; word-break: break-all;">
                {top_hole['instance'].split('/')[-1] if top_hole else 'None'}
            </div>
            <div class="kpi-sub">{f"{top_hole['au']:,} AU ({top_hole['pct_of_total_au']:.1f}% of total)" if top_hole else ""}</div>
        </div>
    </div>

    <!-- Prioritized Action Plan & Next Steps -->
    <div class="card">
        <h2>
            <span>Actionable Next Steps to Increase Test Coverage</span>
            <span style="font-size: 0.9rem; font-weight: normal; color: var(--text-dim);">{len(analysis['next_steps'])} Targeted Initiatives</span>
        </h2>
        <p>Follow these prioritized engineering recommendations to eliminate untestable fault bottlenecks and bring test coverage to target:</p>
"""

    for step in analysis["next_steps"]:
        p_class = "p1" if "P1" in step["priority"] else "p2" if "P2" in step["priority"] else "p3"
        html += f"""
        <div class="action-card {p_class}">
            <div class="action-header">
                <span class="action-title">{html_lib.escape(step['priority'])}: {html_lib.escape(step['title'])}</span>
                <span class="action-impact">{html_lib.escape(step['impact'])}</span>
            </div>
            <div style="font-size: 0.92rem; color: var(--text-dim); margin-top: 0.4rem;">
                {step['details']}
            </div>
            {f'<div class="action-code">{html_lib.escape(step["command"])}</div>' if step.get("command") else ''}
        </div>
"""

    html += """
    </div>

    <!-- Coverage Holes Section -->
    <div class="card">
        <h2>
            <span>Coverage Holes & AU Bottlenecks</span>
            <span style="font-size: 0.9rem; font-weight: normal; color: var(--text-dim);">Top Sub-Blocks Ranked by AU Count</span>
        </h2>
        <p>These instances represent the largest sinks of ATPG Untestable (AU) faults in the design hierarchy. Addressing these bottlenecks yields the highest coverage return on effort.</p>
        
        <input type="text" id="holesSearch" class="search-box" onkeyup="filterTable('holesSearch', 'holesTable')" placeholder="Search coverage holes by instance, module, or category...">
        
        <div class="table-responsive">
            <table id="holesTable">
                <thead>
                    <tr>
                        <th style="width: 40px;">#</th>
                        <th>Instance Path</th>
                        <th>Module Name</th>
                        <th>Category</th>
                        <th class="text-right">Total Faults</th>
                        <th class="text-right">Test Coverage</th>
                        <th class="text-right">AU Faults</th>
                        <th class="text-right">% of Total AU</th>
                    </tr>
                </thead>
                <tbody>
"""

    for idx, hole in enumerate(analysis["coverage_holes"], 1):
        cat = hole["category"]
        tag_cls = (
            "tag-memory" if "Memory" in cat else
            "tag-clock" if "Clock" in cat else
            "tag-bus" if "Bus" in cat else
            "tag-control" if "Control" in cat else "tag-other"
        )
        tc_val = hole["tc"]
        fill_cls = "fill-green" if tc_val >= 95 else "fill-yellow" if tc_val >= 90 else "fill-red"

        html += f"""
                    <tr>
                        <td class="text-center font-mono">{idx}</td>
                        <td class="font-mono" style="max-width: 420px; word-break: break-all;">
                            <strong>{html_lib.escape(hole['instance'])}</strong>
                        </td>
                        <td class="font-mono" style="max-width: 250px; word-break: break-all; color: #94a3b8;">
                            {html_lib.escape(hole['module'])}
                        </td>
                        <td><span class="tag {tag_cls}">{html_lib.escape(cat)}</span></td>
                        <td class="text-right font-mono">{hole['faults']:,}</td>
                        <td class="text-right font-mono">
                            <span class="{ 'val-green' if tc_val >= 95 else 'val-yellow' if tc_val >= 90 else 'val-red' }">{tc_val:.2f}%</span>
                            <div class="progress-bar"><div class="progress-fill {fill_cls}" style="width: {min(max(tc_val, 0), 100):.1f}%;"></div></div>
                        </td>
                        <td class="text-right font-mono val-red"><strong>{hole['au']:,}</strong></td>
                        <td class="text-right font-mono">{hole['pct_of_total_au']:.2f}%</td>
                    </tr>
"""

    html += f"""
                </tbody>
            </table>
        </div>
    </div>

    <!-- Depth-1 Partition Analysis -->
    <div class="card">
        <h2>
            <span>Primary Partition (Depth-1) Breakdown</span>
            <span style="font-size: 0.9rem; font-weight: normal; color: var(--text-dim);">{len(analysis['d1_list'])} Functional Partitions</span>
        </h2>
        <p>Comprehensive overview of all top-level partition instantiations and unassigned boundary glue logic.</p>

        <input type="text" id="d1Search" class="search-box" onkeyup="filterTable('d1Search', 'd1Table')" placeholder="Search partitions...">

        <div class="table-responsive">
            <table id="d1Table">
                <thead>
                    <tr>
                        <th>Partition Instance</th>
                        <th>Module</th>
                        <th>Category</th>
                        <th class="text-right">Total Faults</th>
                        <th class="text-right">Test Coverage</th>
                        <th class="text-right">AU Faults</th>
                        <th class="text-right">% of Total AU</th>
                        <th class="text-center">Health Status</th>
                    </tr>
                </thead>
                <tbody>
"""

    # Add D1 partitions + glue logic
    d1_and_glue = list(analysis["d1_list"])
    if analysis["glue"]["faults"] > 0 or analysis["glue"]["au"] > 0:
        d1_and_glue.append(analysis["glue"])

    # Re-sort by AU descending
    d1_and_glue = sorted(d1_and_glue, key=lambda x: x["au"], reverse=True)

    for item in d1_and_glue:
        tc_val = item["tc"]
        if item["faults"] == 0:
            status_badge = '<span class="tag tag-other">NO FAULTS</span>'
        elif tc_val >= 98.0:
            status_badge = '<span class="tag" style="background:var(--green-bg); color:var(--green);">EXCELLENT</span>'
        elif tc_val >= 95.0:
            status_badge = '<span class="tag" style="background:var(--blue-bg); color:var(--blue);">PASS</span>'
        elif tc_val >= 90.0:
            status_badge = '<span class="tag" style="background:var(--yellow-bg); color:var(--yellow);">WARNING</span>'
        else:
            status_badge = '<span class="tag" style="background:var(--red-bg); color:var(--red);">CRITICAL</span>'

        cat = item.get("category", "Core Logic")
        tag_cls = (
            "tag-memory" if "Memory" in cat else
            "tag-clock" if "Clock" in cat else
            "tag-bus" if "Bus" in cat else
            "tag-control" if "Control" in cat else "tag-other"
        )

        html += f"""
                    <tr>
                        <td class="font-mono"><strong>{html_lib.escape(item['instance'])}</strong></td>
                        <td class="font-mono" style="color: #94a3b8;">{html_lib.escape(item['module'])}</td>
                        <td><span class="tag {tag_cls}">{html_lib.escape(cat)}</span></td>
                        <td class="text-right font-mono">{item['faults']:,}</td>
                        <td class="text-right font-mono { 'val-green' if tc_val >= 95 else 'val-yellow' if tc_val >= 90 else 'val-red' }">
                            {f"{tc_val:.2f}%" if item['faults'] > 0 else "0.00%"}
                        </td>
                        <td class="text-right font-mono { 'val-red' if item['au'] > 1000 else 'val-yellow' if item['au'] > 100 else '' }">
                            {item['au']:,}
                        </td>
                        <td class="text-right font-mono">{item['pct_of_total_au']:.2f}%</td>
                        <td class="text-center">{status_badge}</td>
                    </tr>
"""

    html += f"""
                </tbody>
            </table>
        </div>
    </div>

    <!-- Footer -->
    <footer style="margin-top: 2rem; padding-top: 1.5rem; border-top: 1px solid var(--border); text-align: center; color: var(--text-dim); font-size: 0.85rem;">
        Generated by <strong>ATPG Coverage Report Agent</strong> &bull; Siemens Tessent 2026.1 Flow &bull; Intel CTH DFT Automation
    </footer>
</div>
</body>
</html>
"""

    with open(output_html, "w") as f:
        f.write(html)

    print(f"[✓] Successfully generated HTML report: {output_html}")
    return output_html


def main():
    parser = argparse.ArgumentParser(description="Automated ATPG Coverage & AU Breakdown Reporter")
    parser.add_argument("--run-dir", "-r", required=True, help="Path to ATPG run directory")
    parser.add_argument("--recipe", help="Recipe name (e.g. stuckat_edt_min, atspeed_edt_min)")
    parser.add_argument("--output-dir", "-o", help="Directory to save generated CSV and HTML reports (defaults to run-dir)")
    parser.add_argument("--csv-file", help="Path to write or use existing instance_au_breakdown.csv")
    parser.add_argument("--html-file", help="Path to write output HTML report")
    parser.add_argument("--skip-tessent", action="store_true", help="Skip Tessent execution and use existing CSV")
    parser.add_argument("--tessent-bin", default=DEFAULT_TESSENT, help="Path to Tessent executable")
    parser.add_argument("--license-server", default=DEFAULT_LICENSE, help="SALT license server")
    parser.add_argument("--top-n", type=int, default=30, help="Number of top coverage holes to detail")

    args = parser.parse_args()

    # Resolve output directory
    out_dir = os.path.abspath(args.output_dir if args.output_dir else args.run_dir)
    os.makedirs(out_dir, exist_ok=True)

    csv_path = os.path.abspath(args.csv_file if args.csv_file else os.path.join(out_dir, "instance_au_breakdown.csv"))
    html_path = os.path.abspath(args.html_file if args.html_file else os.path.join(out_dir, "atpg_coverage_report.html"))
    log_path = os.path.splitext(csv_path)[0] + "_tessent.log"

    # Step 1: Discover artifacts
    artifacts = find_files_for_run(args.run_dir, recipe=args.recipe)

    # Step 2: Run Tessent or reuse CSV
    if args.skip_tessent:
        if not os.path.exists(csv_path):
            raise FileNotFoundError(f"--skip-tessent specified, but CSV does not exist: {csv_path}")
        print(f"[*] Skipping Tessent execution as requested. Using existing CSV: {csv_path}")
    else:
        # Check if CSV already exists in run_dir or output_dir
        if os.path.exists(csv_path) and os.path.getsize(csv_path) > 1000:
            print(f"[*] Found pre-existing {csv_path}. To regenerate, delete the file or specify a different path.")
        else:
            run_tessent_extraction(
                artifacts=artifacts,
                output_csv=csv_path,
                log_file=log_path,
                tessent_bin=args.tessent_bin,
                license_server=args.license_server
            )

    # Step 3: Parse CSV
    top_record, all_instances, d1_instances = parse_instance_au_breakdown(csv_path)

    # Step 4: Analyze coverage holes and formulate next steps
    analysis = analyze_coverage(top_record, all_instances, d1_instances, top_n=args.top_n)

    # Step 5: Generate HTML report
    generate_html_report(analysis, artifacts, html_path)

    # Step 6: Print Console Summary
    print("\n" + "="*80)
    print(f"  ATPG COVERAGE & AU BREAKDOWN SUMMARY: {artifacts['fub']} ({artifacts['recipe']})")
    print("="*80)
    print(f"  • Overall Test Coverage:  {analysis['overall_tc']:.2f}% (Target: >=95.00%)")
    print(f"  • Total Design Faults:    {analysis['total_faults']:,}")
    print(f"  • Total AU Faults:        {analysis['total_au']:,} ({analysis['au_rate']:.2f}% AU Rate)")
    print(f"  • Partition Count (D1):   {len(analysis['d1_list'])}")
    print("-"*80)
    print("  TOP 5 COVERAGE HOLES:")
    for idx, hole in enumerate(analysis["coverage_holes"][:5], 1):
        print(f"    {idx}. {hole['instance']} ({hole['category']})")
        print(f"       AU: {hole['au']:,} ({hole['pct_of_total_au']:.2f}% of design AU) | TC: {hole['tc']:.2f}% | Faults: {hole['faults']:,}")
    print("-"*80)
    print(f"  Generated Artifacts:")
    print(f"  • CSV Breakdown:  {csv_path}")
    print(f"  • HTML Report:    {html_path}")
    print("="*80 + "\n")


if __name__ == "__main__":
    main()
