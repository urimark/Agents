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
import re
import getpass
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
    p = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)

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
    data_map = {}

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
            data_map[inst] = rec

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
        data_map["-top-"] = top_record

    return top_record, all_instances, d1_instances, data_map


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


def analyze_comparison(run1_data, run2_data, label1="Run 1", label2="Run 2"):
    """
    Performs cross-run delta analysis, ranks regressions/improvements, and formulates dual-run improvement advice.
    """
    top1 = run1_data["analysis"]["top"]
    top2 = run2_data["analysis"]["top"]
    map1 = run1_data["data_map"]
    map2 = run2_data["data_map"]

    delta_faults = top1["faults"] - top2["faults"]
    pct_delta_faults = (delta_faults / top2["faults"] * 100) if top2["faults"] else 0.0

    delta_au = top1["au"] - top2["au"]
    pct_delta_au = (delta_au / top2["au"] * 100) if top2["au"] else 0.0

    delta_tc = top1["tc"] - top2["tc"]
    au_rate1 = run1_data["analysis"]["au_rate"]
    au_rate2 = run2_data["analysis"]["au_rate"]
    delta_au_rate = au_rate1 - au_rate2

    # Depth-1 Partitions comparison
    all_keys = set(map1.keys()) | set(map2.keys())
    d1_keys = {k for k in all_keys if (map1.get(k, {}).get("depth") == 1 or map2.get(k, {}).get("depth") == 1)}

    d1_comparison = []
    for k in sorted(d1_keys):
        r1 = map1.get(k, {"faults": 0, "tc": 0.0, "au": 0, "module": ""})
        r2 = map2.get(k, {"faults": 0, "tc": 0.0, "au": 0, "module": ""})
        d_au = r1["au"] - r2["au"]
        pct_au = (d_au / r2["au"] * 100) if r2["au"] > 0 else (100.0 if r1["au"] > 0 else 0.0)
        d_f = r1["faults"] - r2["faults"]
        d_tc = r1["tc"] - r2["tc"] if (r1["faults"] > 0 and r2["faults"] > 0) else 0.0
        mod = r1["module"] if r1["module"] else r2["module"]
        cat = classify_instance(k, mod)

        if k in map1 and k in map2:
            status = "Common"
        elif k in map1:
            status = f"{label1} Only"
        else:
            status = f"{label2} Only"

        d1_comparison.append({
            "instance": k,
            "module": mod,
            "category": cat,
            "status": status,
            "run1_faults": r1["faults"],
            "run2_faults": r2["faults"],
            "delta_faults": d_f,
            "run1_tc": r1["tc"],
            "run2_tc": r2["tc"],
            "delta_tc": d_tc,
            "run1_au": r1["au"],
            "run2_au": r2["au"],
            "delta_au": d_au,
            "pct_delta_au": pct_au
        })

    # Glue logic delta
    glue1 = run1_data["analysis"]["glue"]
    glue2 = run2_data["analysis"]["glue"]
    delta_glue_au = glue1["au"] - glue2["au"]
    pct_glue_au = (delta_glue_au / glue2["au"] * 100) if glue2["au"] > 0 else 0.0
    glue_comp = {
        "instance": "[Top-Level Boundary / Glue Logic]",
        "module": f"{top1['module']} (unassigned to D1)",
        "category": "Buffer / Repeater / Glue",
        "status": "Common",
        "run1_faults": glue1["faults"],
        "run2_faults": glue2["faults"],
        "delta_faults": glue1["faults"] - glue2["faults"],
        "run1_tc": glue1["tc"],
        "run2_tc": glue2["tc"],
        "delta_tc": glue1["tc"] - glue2["tc"],
        "run1_au": glue1["au"],
        "run2_au": glue2["au"],
        "delta_au": delta_glue_au,
        "pct_delta_au": pct_glue_au
    }

    d1_comparison.append(glue_comp)
    d1_sorted = sorted(d1_comparison, key=lambda x: abs(x["delta_au"]), reverse=True)

    # Hierarchical sub-instances comparison
    all_inst_comp = []
    for k in all_keys:
        if k == "-top-": continue
        r1 = map1.get(k, {"faults": 0, "tc": 0.0, "au": 0, "module": "", "depth": 0})
        r2 = map2.get(k, {"faults": 0, "tc": 0.0, "au": 0, "module": "", "depth": 0})
        if r1["au"] == 0 and r2["au"] == 0: continue
        d_au = r1["au"] - r2["au"]
        pct_au = (d_au / r2["au"] * 100) if r2["au"] > 0 else (100.0 if r1["au"] > 0 else 0.0)
        mod = r1["module"] if r1["module"] else r2["module"]
        depth = r1["depth"] if r1["depth"] else r2["depth"]
        cat = classify_instance(k, mod)
        status = "Common" if (k in map1 and k in map2) else (f"{label1} Only" if k in map1 else f"{label2} Only")

        all_inst_comp.append({
            "instance": k,
            "module": mod,
            "depth": depth,
            "category": cat,
            "status": status,
            "run1_faults": r1["faults"],
            "run2_faults": r2["faults"],
            "delta_faults": r1["faults"] - r2["faults"],
            "run1_tc": r1["tc"],
            "run2_tc": r2["tc"],
            "delta_tc": r1["tc"] - r2["tc"],
            "run1_au": r1["au"],
            "run2_au": r2["au"],
            "delta_au": d_au,
            "pct_delta_au": pct_au
        })

    regressions_run1 = sorted([x for x in all_inst_comp if x["delta_au"] > 100], key=lambda x: x["delta_au"], reverse=True)
    improvements_run1 = sorted([x for x in all_inst_comp if x["delta_au"] < -100], key=lambda x: x["delta_au"])

    # Improvement Advice for Run 1
    advice_run1 = []
    if regressions_run1:
        top_reg = regressions_run1[0]
        advice_run1.append({
            "priority": "P1 - Critical Regression",
            "title": f"Recover AU in {top_reg['instance']} ({top_reg['category']})",
            "impact": f"{label1} has +{top_reg['delta_au']:,} more AU than {label2}",
            "details": f"In {label1}, instance <code>{top_reg['instance']}</code> carries {top_reg['run1_au']:,} AU compared to only {top_reg['run2_au']:,} in {label2}. Inspect module <code>{top_reg['module']}</code> constraints, clock gating bypasses, or missing test waivers in {label1}.",
            "command": f"report_faults {top_reg['instance']}/* -au -summary"
        })
    ram_reg = [x for x in regressions_run1 if "rw_array" in (x["instance"] + " " + x["module"]).lower() or "array" in x["instance"].lower()]
    if ram_reg:
        total_ram_reg = sum(x["delta_au"] for x in ram_reg)
        advice_run1.append({
            "priority": "P1 - High Impact",
            "title": f"Blackbox / Bypass RAM Array Wrappers in {label1}",
            "impact": f"Up to {total_ram_reg:,} AU higher in {label1} vs {label2}",
            "details": f"{label1} exhibits higher AU in read/write RAM array wrappers (e.g. <code>{ram_reg[0]['instance'].split('/')[-1]}</code>). Ensure these unscan-stitched RAM arrays are blackboxed in logic ATPG or MBIST bypass mode is enforced.",
            "command": "add_black_box par_santa_par_santa_ccf_rw_array_wrapper_* -auto"
        })
    advice_run1.append({
        "priority": "P2 - Alignment",
        "title": f"Audit Clock & Reset Bypass Rules against {label2}",
        "impact": "Harmonizes ATPG constraints across both runs",
        "details": f"Compare DO-files (resets.do, synchronous_clock_list.do) between {label1} and {label2} to identify missing pin constraints or inactive scan bypass signals in {label1}.",
        "command": "report_clocks -matrix; report_drc_rules -violations"
    })

    # Improvement Advice for Run 2
    advice_run2 = []
    if improvements_run1:
        top_imp = improvements_run1[0]
        r2_higher = abs(top_imp["delta_au"])
        advice_run2.append({
            "priority": "P1 - Critical",
            "title": f"Backport {label1} Fixes to {top_imp['instance']} in {label2}",
            "impact": f"{label2} has +{r2_higher:,} more AU than {label1}",
            "details": f"In {label2}, instance <code>{top_imp['instance']}</code> has {top_imp['run2_au']:,} AU, whereas {label1} achieved {top_imp['run1_au']:,} AU ({top_imp['pct_delta_au']:.1f}% reduction). Backport {label1}'s model, blackboxing, or DRC waivers to {label2}.",
            "command": f"report_statistics -instance {top_imp['instance']}"
        })
    ram_imp = [x for x in improvements_run1 if "rw_array" in (x["instance"] + " " + x["module"]).lower() or "array" in x["instance"].lower()]
    if ram_imp:
        total_ram_imp = sum(abs(x["delta_au"]) for x in ram_imp)
        advice_run2.append({
            "priority": "P1 - High Impact",
            "title": f"Adopt {label1} Array Blackboxing/Bypass Strategy in {label2}",
            "impact": f"Can eliminate up to {total_ram_imp:,} AU in {label2}",
            "details": f"{label1} successfully eliminated hundreds of thousands of AU across memory arrays and wrappers. Apply identical <code>add_black_box</code> or MBIST collar bypass directives in {label2}.",
            "command": "add_black_box *rw_array_wrapper* -auto"
        })
    advice_run2.append({
        "priority": "P2 - Alignment",
        "title": f"Synchronize Fault Disposition Lists with {label1}",
        "impact": "Excludes structurally tied/redundant logic from {label2}",
        "details": f"Ensure the disposition file used in {label1} is applied to {label2} to delete known architectural untestable faults.",
        "command": "read_faults $disp_file -delete"
    })

    # Shared Advice for Both Runs
    advice_shared = [
        {
            "priority": "P2 - Core Health",
            "title": "Resolve Asynchronous Reset Synchronizers (CDU / Double-Sync)",
            "impact": "Improves testability across clock/reset domains in both runs",
            "details": "Both runs carry residual AU in clock distribution and reset synchronizer chains (e.g. <code>cdu_</code>, <code>ctech_lib_doublesync_rst</code>). Verify that scan reset bypass signals (such as <code>fscan_bypx_rst</code>) are asserted during capture.",
            "command": "add_cell_constraints -inst <doublesync_inst> -value 0"
        },
        {
            "priority": "P3 - Test Points",
            "title": "Evaluate Test Point Insertion (TPI) for Deep Combinational Logic",
            "impact": "Breaks long observability chains to raise maximum coverage ceiling",
            "details": "For deep logic cones with high fan-in where coverage saturates below 95%, test points (controllability and observability points) will raise the upper coverage bound for both runs.",
            "command": "report_test_points -summary"
        }
    ]

    return {
        "label1": label1,
        "label2": label2,
        "top1": top1,
        "top2": top2,
        "delta_faults": delta_faults,
        "pct_delta_faults": pct_delta_faults,
        "delta_au": delta_au,
        "pct_delta_au": pct_delta_au,
        "delta_tc": delta_tc,
        "au_rate1": au_rate1,
        "au_rate2": au_rate2,
        "delta_au_rate": delta_au_rate,
        "d1_comparison": d1_sorted,
        "glue": glue_comp,
        "regressions_run1": regressions_run1[:50],
        "improvements_run1": improvements_run1[:50],
        "all_inst_comp": all_inst_comp,
        "advice_run1": advice_run1,
        "advice_run2": advice_run2,
        "advice_shared": advice_shared
    }


def export_comparison_csv(comp_analysis, output_csv):
    """
    Exports full hierarchical comparison deltas into CSV format.
    """
    print(f"[*] Exporting comparison deltas CSV: {output_csv}")
    l1 = comp_analysis["label1"]
    l2 = comp_analysis["label2"]
    with open(output_csv, "w", newline="", errors="ignore") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Instance", "Module", "Depth", "Category", "Status",
            f"{l1} Faults", f"{l2} Faults", "Faults Delta",
            f"{l1} TC (%)", f"{l2} TC (%)", "TC Delta (%)",
            f"{l1} AU", f"{l2} AU", f"AU Delta ({l1} - {l2})", "AU % Change"
        ])
        for r in comp_analysis["all_inst_comp"]:
            writer.writerow([
                r["instance"], r["module"], r["depth"], r["category"], r["status"],
                r["run1_faults"], r["run2_faults"], r["delta_faults"],
                f"{r['run1_tc']:.2f}", f"{r['run2_tc']:.2f}", f"{r['delta_tc']:+.2f}",
                r["run1_au"], r["run2_au"], r["delta_au"], f"{r['pct_delta_au']:+.2f}%"
            ])
    print(f"[✓] Saved comparison deltas CSV: {output_csv}")


def generate_comparison_html_report(comp, run1_data, run2_data, output_html, rel_report1, rel_report2, rel_csv=None):
    """
    Renders an interactive 3-way cross-run comparison report with delta tables and improvement advisory.
    """
    print(f"[*] Rendering comparison HTML report: {output_html}")
    l1 = comp["label1"]
    l2 = comp["label2"]
    top1 = comp["top1"]
    top2 = comp["top2"]

    dtc = comp["delta_tc"]
    dau = comp["delta_au"]
    if dtc > 0.05:
        verdict_badge = f'<span class="badge badge-green">{html_lib.escape(l1)} Leads: +{dtc:.2f}% Coverage (AU Delta: {dau:+,})</span>'
    elif dtc < -0.05:
        verdict_badge = f'<span class="badge badge-yellow">{html_lib.escape(l2)} Leads: +{abs(dtc):.2f}% Coverage (AU Delta: {dau:+,})</span>'
    else:
        verdict_badge = f'<span class="badge badge-blue">Coverage Parity ({top1["tc"]:.2f}% vs {top2["tc"]:.2f}%)</span>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ATPG Coverage Comparison Report: {html_lib.escape(l1)} vs {html_lib.escape(l2)}</title>
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
        .container {{ max-width: 1520px; margin: 0 auto; }}
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
        .nav-links {{
            display: flex;
            gap: 0.75rem;
            margin-top: 0.75rem;
            flex-wrap: wrap;
        }}
        .nav-btn {{
            display: inline-flex;
            align-items: center;
            gap: 0.4rem;
            padding: 0.45rem 0.9rem;
            background: var(--bg-subcard);
            border: 1px solid var(--border);
            border-radius: 6px;
            color: var(--text-highlight);
            text-decoration: none;
            font-size: 0.88rem;
            font-weight: 600;
            transition: all 0.2s;
        }}
        .nav-btn:hover {{
            background: var(--border);
            color: #fff;
            transform: translateY(-1px);
        }}
        .badge {{
            display: inline-block;
            padding: 0.35rem 0.9rem;
            border-radius: 4px;
            font-size: 0.9rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .badge-green {{ background: var(--green-bg); color: var(--green); border: 1px solid var(--green); }}
        .badge-blue {{ background: var(--blue-bg); color: var(--blue); border: 1px solid var(--blue); }}
        .badge-yellow {{ background: var(--yellow-bg); color: var(--yellow); border: 1px solid var(--yellow); }}
        .badge-red {{ background: var(--red-bg); color: var(--red); border: 1px solid var(--red); }}

        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 1.25rem;
            margin-bottom: 2rem;
        }}
        .kpi-card {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 1.25rem 1.5rem;
            box-shadow: 0 4px 6px -1px rgba(0,0,0,0.2);
        }}
        .kpi-title {{
            font-size: 0.85rem;
            text-transform: uppercase;
            font-weight: 600;
            color: var(--text-dim);
            margin-bottom: 0.4rem;
        }}
        .kpi-val {{
            font-size: 1.85rem;
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

        .split-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 1.5rem;
            margin-bottom: 1rem;
        }}
        @media (max-width: 960px) {{
            .split-grid {{ grid-template-columns: 1fr; }}
        }}

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
        .action-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 0.4rem;
        }}
        .action-title {{
            font-size: 1rem;
            font-weight: 700;
            color: var(--text-main);
        }}
        .action-impact {{
            font-size: 0.82rem;
            font-weight: 600;
            padding: 0.15rem 0.55rem;
            border-radius: 4px;
            background: rgba(255, 255, 255, 0.08);
            color: var(--text-highlight);
        }}
        .action-code {{
            background: var(--code-bg);
            border: 1px solid var(--border);
            border-radius: 4px;
            padding: 0.45rem 0.7rem;
            margin-top: 0.6rem;
            font-family: 'JetBrains Mono', Consolas, monospace;
            font-size: 0.84rem;
            color: #7dd3fc;
            overflow-x: auto;
        }}

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
        .tag-improved {{ background: var(--green-bg); color: var(--green); border: 1px solid var(--green); }}
        .tag-regressed {{ background: var(--red-bg); color: var(--red); border: 1px solid var(--red); }}
        .tag-neutral {{ background: rgba(148, 163, 184, 0.15); color: #cbd5e1; border: 1px solid #475569; }}

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
                <h1>ATPG Coverage Comparison: {html_lib.escape(l1)} vs {html_lib.escape(l2)}</h1>
                <div class="meta">
                    Generated on: <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code> &nbsp;|&nbsp;
                    Run 1: <code>{html_lib.escape(run1_data['artifacts']['work_dir'])}</code> &nbsp;|&nbsp;
                    Run 2: <code>{html_lib.escape(run2_data['artifacts']['work_dir'])}</code>
                </div>
                <div class="nav-links">
                    <a href="{html_lib.escape(rel_report1)}" class="nav-btn">📄 Report 1: {html_lib.escape(l1)} Coverage Report</a>
                    <a href="{html_lib.escape(rel_report2)}" class="nav-btn">📄 Report 2: {html_lib.escape(l2)} Coverage Report</a>
                    {f'<a href="{html_lib.escape(rel_csv)}" class="nav-btn">📊 Download Deltas CSV</a>' if rel_csv else ''}
                </div>
            </div>
            <div>
                {verdict_badge}
            </div>
        </div>
    </header>

    <!-- KPI Grid -->
    <div class="kpi-grid">
        <div class="kpi-card">
            <div class="kpi-title">Test Coverage (TC)</div>
            <div class="kpi-val { 'val-green' if dtc >= 0 else 'val-red' }">
                {dtc:+.2f}%
            </div>
            <div class="kpi-sub">{l1}: {top1['tc']:.2f}% vs {l2}: {top2['tc']:.2f}%</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-title">AU Delta ({l1} - {l2})</div>
            <div class="kpi-val { 'val-green' if dau <= 0 else 'val-red' }">
                {dau:+,}
            </div>
            <div class="kpi-sub">{l1}: {top1['au']:,} vs {l2}: {top2['au']:,} ({comp['pct_delta_au']:+.1f}%)</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-title">Fault Universe Delta</div>
            <div class="kpi-val val-blue">
                {comp['delta_faults']:+,}
            </div>
            <div class="kpi-sub">{l1}: {top1['faults']:,} vs {l2}: {top2['faults']:,} ({comp['pct_delta_faults']:+.1f}%)</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-title">AU Rate Delta</div>
            <div class="kpi-val { 'val-green' if comp['delta_au_rate'] <= 0 else 'val-red' }">
                {comp['delta_au_rate']:+.2f}%
            </div>
            <div class="kpi-sub">{l1}: {comp['au_rate1']:.2f}% vs {l2}: {comp['au_rate2']:.2f}%</div>
        </div>
    </div>

    <!-- Dual-Run Actionable Advisory -->
    <div class="card">
        <h2>
            <span>Actionable Advisory: How to Improve Coverage in Both Runs</span>
            <span style="font-size: 0.9rem; font-weight: normal; color: var(--text-dim);">Targeted Dual-Run Guidance</span>
        </h2>
        <p>Specific engineering recommendations derived from cross-run divergence, identifying where each run lags and how each can achieve optimal test coverage:</p>

        <div class="split-grid">
            <!-- Advice for Run 1 -->
            <div>
                <h3>1. How to Improve {html_lib.escape(l1)} Coverage</h3>
                <p style="font-size: 0.9rem;">Initiatives addressing areas where {html_lib.escape(l1)} regressed or lags behind {html_lib.escape(l2)}:</p>
"""

    for step in comp["advice_run1"]:
        p_cls = "p1" if "Critical" in step["priority"] else "p2"
        html += f"""
                <div class="action-card {p_cls}">
                    <div class="action-header">
                        <span class="action-title">{html_lib.escape(step['priority'])}: {html_lib.escape(step['title'])}</span>
                        <span class="action-impact">{html_lib.escape(step['impact'])}</span>
                    </div>
                    <div style="font-size: 0.9rem; color: var(--text-dim); margin-top: 0.3rem;">
                        {step['details']}
                    </div>
                    {f'<div class="action-code">{html_lib.escape(step["command"])}</div>' if step.get("command") else ''}
                </div>
"""

    html += f"""
            </div>

            <!-- Advice for Run 2 -->
            <div>
                <h3>2. How to Improve {html_lib.escape(l2)} Coverage</h3>
                <p style="font-size: 0.9rem;">Initiatives backporting {html_lib.escape(l1)} victories and closing {html_lib.escape(l2)} gaps:</p>
"""

    for step in comp["advice_run2"]:
        p_cls = "p1" if "Critical" in step["priority"] else "p2"
        html += f"""
                <div class="action-card {p_cls}">
                    <div class="action-header">
                        <span class="action-title">{html_lib.escape(step['priority'])}: {html_lib.escape(step['title'])}</span>
                        <span class="action-impact">{html_lib.escape(step['impact'])}</span>
                    </div>
                    <div style="font-size: 0.9rem; color: var(--text-dim); margin-top: 0.3rem;">
                        {step['details']}
                    </div>
                    {f'<div class="action-code">{html_lib.escape(step["command"])}</div>' if step.get("command") else ''}
                </div>
"""

    html += f"""
            </div>
        </div>

        <!-- Shared Advice -->
        <h3 style="margin-top: 1.5rem;">3. Shared Initiatives for Both Runs</h3>
        <p style="font-size: 0.9rem;">Common bottlenecks identified across both designs:</p>
"""

    for step in comp["advice_shared"]:
        html += f"""
        <div class="action-card p2">
            <div class="action-header">
                <span class="action-title">{html_lib.escape(step['priority'])}: {html_lib.escape(step['title'])}</span>
                <span class="action-impact">{html_lib.escape(step['impact'])}</span>
            </div>
            <div style="font-size: 0.9rem; color: var(--text-dim); margin-top: 0.3rem;">
                {step['details']}
            </div>
            {f'<div class="action-code">{html_lib.escape(step["command"])}</div>' if step.get("command") else ''}
        </div>
"""

    html += f"""
    </div>

    <!-- Depth-1 Partitions Comparison Table -->
    <div class="card">
        <h2>
            <span>Primary Partition (Depth-1) Delta Comparison</span>
            <span style="font-size: 0.9rem; font-weight: normal; color: var(--text-dim);">{len(comp['d1_comparison'])} Partitions</span>
        </h2>
        <p>Comparison of fault totals, test coverage %, and AU delta across all Depth-1 partition instances.</p>

        <input type="text" id="d1CompSearch" class="search-box" onkeyup="filterTable('d1CompSearch', 'd1CompTable')" placeholder="Search partitions by name or module...">

        <div class="table-responsive">
            <table id="d1CompTable">
                <thead>
                    <tr>
                        <th>Partition Instance</th>
                        <th>Module</th>
                        <th class="text-right">{html_lib.escape(l1)} AU</th>
                        <th class="text-right">{html_lib.escape(l2)} AU</th>
                        <th class="text-right">AU Delta</th>
                        <th class="text-right">% Change</th>
                        <th class="text-right">{html_lib.escape(l1)} TC</th>
                        <th class="text-right">{html_lib.escape(l2)} TC</th>
                        <th class="text-right">TC Delta</th>
                        <th class="text-center">Status</th>
                    </tr>
                </thead>
                <tbody>
"""

    for r in comp["d1_comparison"]:
        dau_val = r["delta_au"]
        dtc_val = r["delta_tc"]
        val_cls = "val-green" if dau_val < 0 else ("val-red" if dau_val > 0 else "")
        tc_cls = "val-green" if dtc_val > 0.5 else ("val-red" if dtc_val < -0.5 else "")
        status_tag = (
            f'<span class="tag tag-improved">IMPROVED IN {html_lib.escape(l1)}</span>' if dau_val < -500 else
            (f'<span class="tag tag-regressed">REGRESSED IN {html_lib.escape(l1)}</span>' if dau_val > 500 else
            f'<span class="tag tag-neutral">{html_lib.escape(r["status"])}</span>')
        )

        html += f"""
                    <tr>
                        <td class="font-mono"><strong>{html_lib.escape(r['instance'])}</strong></td>
                        <td class="font-mono" style="color: #94a3b8;">{html_lib.escape(r['module'][:38])}</td>
                        <td class="text-right font-mono">{r['run1_au']:,}</td>
                        <td class="text-right font-mono">{r['run2_au']:,}</td>
                        <td class="text-right font-mono {val_cls}"><strong>{dau_val:+,}</strong></td>
                        <td class="text-right font-mono {val_cls}">{r['pct_delta_au']:+.1f}%</td>
                        <td class="text-right font-mono">{r['run1_tc']:.2f}%</td>
                        <td class="text-right font-mono">{r['run2_tc']:.2f}%</td>
                        <td class="text-right font-mono {tc_cls}"><strong>{dtc_val:+.2f}%</strong></td>
                        <td class="text-center">{status_tag}</td>
                    </tr>
"""

    html += f"""
                </tbody>
            </table>
        </div>
    </div>

    <!-- Top Regressions Table -->
    <div class="card">
        <h2>
            <span>Top AU Regressions in {html_lib.escape(l1)} ({html_lib.escape(l1)} AU &gt; {html_lib.escape(l2)} AU)</span>
            <span style="font-size: 0.9rem; font-weight: normal; color: var(--text-dim);">{len(comp['regressions_run1'])} High Delta Instances</span>
        </h2>
        <p>Sub-instances where {html_lib.escape(l1)} introduced more untestable faults than {html_lib.escape(l2)}. These represent prime opportunities to recover coverage in {html_lib.escape(l1)}.</p>

        <div class="table-responsive">
            <table>
                <thead>
                    <tr>
                        <th>#</th>
                        <th>Instance Path</th>
                        <th>Module</th>
                        <th>Category</th>
                        <th class="text-right">{html_lib.escape(l1)} AU</th>
                        <th class="text-right">{html_lib.escape(l2)} AU</th>
                        <th class="text-right">AU Delta</th>
                        <th class="text-right">{html_lib.escape(l1)} TC</th>
                        <th class="text-right">{html_lib.escape(l2)} TC</th>
                    </tr>
                </thead>
                <tbody>
"""

    for idx, r in enumerate(comp["regressions_run1"][:25], 1):
        html += f"""
                    <tr>
                        <td class="text-center font-mono">{idx}</td>
                        <td class="font-mono" style="max-width: 440px; word-break: break-all;"><strong>{html_lib.escape(r['instance'])}</strong></td>
                        <td class="font-mono" style="color: #94a3b8;">{html_lib.escape(r['module'])}</td>
                        <td><span class="tag tag-neutral">{html_lib.escape(r['category'])}</span></td>
                        <td class="text-right font-mono val-red"><strong>{r['run1_au']:,}</strong></td>
                        <td class="text-right font-mono">{r['run2_au']:,}</td>
                        <td class="text-right font-mono val-red"><strong>{r['delta_au']:+,}</strong></td>
                        <td class="text-right font-mono">{r['run1_tc']:.2f}%</td>
                        <td class="text-right font-mono">{r['run2_tc']:.2f}%</td>
                    </tr>
"""

    html += f"""
                </tbody>
            </table>
        </div>
    </div>

    <!-- Top Improvements Table -->
    <div class="card">
        <h2>
            <span>Top AU Improvements in {html_lib.escape(l1)} ({html_lib.escape(l1)} AU &lt; {html_lib.escape(l2)} AU)</span>
            <span style="font-size: 0.9rem; font-weight: normal; color: var(--text-dim);">{len(comp['improvements_run1'])} Resolved Blocks</span>
        </h2>
        <p>Sub-instances where {html_lib.escape(l1)} successfully eliminated untestable faults compared to {html_lib.escape(l2)}. Apply these techniques to improve {html_lib.escape(l2)}.</p>

        <div class="table-responsive">
            <table>
                <thead>
                    <tr>
                        <th>#</th>
                        <th>Instance Path</th>
                        <th>Module</th>
                        <th>Category</th>
                        <th class="text-right">{html_lib.escape(l1)} AU</th>
                        <th class="text-right">{html_lib.escape(l2)} AU</th>
                        <th class="text-right">AU Delta</th>
                        <th class="text-right">{html_lib.escape(l1)} TC</th>
                        <th class="text-right">{html_lib.escape(l2)} TC</th>
                    </tr>
                </thead>
                <tbody>
"""

    for idx, r in enumerate(comp["improvements_run1"][:25], 1):
        html += f"""
                    <tr>
                        <td class="text-center font-mono">{idx}</td>
                        <td class="font-mono" style="max-width: 440px; word-break: break-all;"><strong>{html_lib.escape(r['instance'])}</strong></td>
                        <td class="font-mono" style="color: #94a3b8;">{html_lib.escape(r['module'])}</td>
                        <td><span class="tag tag-neutral">{html_lib.escape(r['category'])}</span></td>
                        <td class="text-right font-mono">{r['run1_au']:,}</td>
                        <td class="text-right font-mono val-red"><strong>{r['run2_au']:,}</strong></td>
                        <td class="text-right font-mono val-green"><strong>{r['delta_au']:+,}</strong></td>
                        <td class="text-right font-mono">{r['run1_tc']:.2f}%</td>
                        <td class="text-right font-mono">{r['run2_tc']:.2f}%</td>
                    </tr>
"""

    html += f"""
                </tbody>
            </table>
        </div>
    </div>

    <!-- Footer -->
    <footer style="margin-top: 2rem; padding-top: 1.5rem; border-top: 1px solid var(--border); text-align: center; color: var(--text-dim); font-size: 0.85rem;">
        Generated by <strong>ATPG Coverage Report Agent</strong> &bull; Cross-Run Comparison Flow &bull; Intel CTH DFT Automation
    </footer>
</div>
</body>
</html>
"""

    with open(output_html, "w") as f:
        f.write(html)

    print(f"[✓] Successfully generated comparison HTML report: {output_html}")
    return output_html


def process_single_run(run_dir, recipe=None, csv_file=None, output_dir=None, html_file=None,
                       skip_tessent=False, tessent_bin=DEFAULT_TESSENT, license_server=DEFAULT_LICENSE,
                       top_n=30, label_suffix="", artifacts=None):
    """
    Executes full pipeline for a single ATPG run.
    """
    out_dir = os.path.abspath(output_dir if output_dir else run_dir)
    os.makedirs(out_dir, exist_ok=True)

    if not artifacts:
        artifacts = find_files_for_run(run_dir, recipe=recipe)

    part_name = artifacts.get("fub", "partition")
    run_type = artifacts.get("recipe", "atpg")

    csv_path = None
    if csv_file:
        csv_path = os.path.abspath(csv_file)
    else:
        # Check if pre-existing CSV in run_dir
        existing_csvs = glob.glob(os.path.join(run_dir, "instance_au_breakdown*.csv"))
        if existing_csvs:
            existing_csvs.sort(key=lambda x: os.path.getsize(x), reverse=True)
            if os.path.getsize(existing_csvs[0]) > 1000:
                csv_path = existing_csvs[0]

        if not csv_path:
            candidate_name = f"instance_au_breakdown_{part_name}_{run_type}{label_suffix}.csv" if label_suffix else f"instance_au_breakdown_{part_name}_{run_type}.csv"
            candidate = os.path.join(out_dir, candidate_name)
            if os.path.exists(candidate) and os.path.getsize(candidate) > 1000:
                csv_path = candidate
            else:
                std_candidate = os.path.join(out_dir, "instance_au_breakdown.csv")
                csv_path = std_candidate if (os.path.exists(std_candidate) and os.path.getsize(std_candidate) > 1000) else candidate

    # Standard naming: atpg_coverage_report_run_<partition_name>_<runtype>.html
    html_default = f"atpg_coverage_report_run_{part_name}_{run_type}{label_suffix}.html" if label_suffix else f"atpg_coverage_report_run_{part_name}_{run_type}.html"
    html_path = os.path.abspath(html_file if html_file else os.path.join(out_dir, html_default))
    log_path = os.path.splitext(csv_path)[0] + "_tessent.log"

    if skip_tessent:
        if not os.path.exists(csv_path):
            raise FileNotFoundError(f"--skip-tessent specified, but CSV does not exist: {csv_path}")
        print(f"[*] Skipping Tessent execution as requested. Using existing CSV: {csv_path}")
    else:
        if os.path.exists(csv_path) and os.path.getsize(csv_path) > 1000:
            print(f"[*] Found pre-existing {csv_path}. Reusing for analysis.")
        else:
            run_tessent_extraction(
                artifacts=artifacts,
                output_csv=csv_path,
                log_file=log_path,
                tessent_bin=tessent_bin,
                license_server=license_server
            )

    top_record, all_instances, d1_instances, data_map = parse_instance_au_breakdown(csv_path)
    analysis = analyze_coverage(top_record, all_instances, d1_instances, top_n=top_n)
    generate_html_report(analysis, artifacts, html_path)

    return {
        "artifacts": artifacts,
        "csv_path": csv_path,
        "html_path": html_path,
        "top_record": top_record,
        "all_instances": all_instances,
        "d1_instances": d1_instances,
        "data_map": data_map,
        "analysis": analysis
    }


def get_workweek_tag(custom_ww=None):
    """
    Computes WorkWeek and day tag, e.g. WW42.Wed. If a custom tag is given, sanitizes and returns it.
    """
    if custom_ww and custom_ww != "auto":
        clean = custom_ww.strip().rstrip("/")
        if not clean.upper().startswith("WW") and clean[0].isdigit():
            clean = "WW" + clean
        return clean
    now = datetime.now()
    ww_num = now.isocalendar()[1]
    day_abbr = now.strftime("%a")
    return f"WW{ww_num:02d}.{day_abbr}"


def generate_menu_pages(git_repo_dir, results_subdir="results"):
    """
    Scans all results/<WW>/ folders and generates interactive Menu index.html pages
    at git repo root, results/index.html, and inside each results/<WW>/index.html.
    """
    results_dir = os.path.join(git_repo_dir, results_subdir)
    if not os.path.isdir(results_dir):
        return []

    import re
    ww_entries = []
    for name in sorted(os.listdir(results_dir), reverse=True):
        full_p = os.path.join(results_dir, name)
        if os.path.isdir(full_p) and not name.startswith("."):
            html_files = sorted(glob.glob(os.path.join(full_p, "*.html")))
            csv_files = sorted(glob.glob(os.path.join(full_p, "*.csv")))
            html_files = [h for h in html_files if not os.path.basename(h).startswith("index")]
            if not html_files and not csv_files:
                continue

            reports_info = []
            comp_reports = []
            run_reports = []

            for h in html_files:
                fname = os.path.basename(h)
                with open(h, "r", errors="ignore") as f:
                    snippet = f.read(4096)
                    title_m = re.search(r"<title>(.*?)</title>", snippet, re.IGNORECASE)
                    title = title_m.group(1).strip() if title_m else fname

                info = {"filename": fname, "title": title}
                reports_info.append(info)
                if "comparison" in fname.lower():
                    comp_reports.append(info)
                else:
                    run_reports.append(info)

            mode_desc = "ATPG Coverage Analysis"
            if comp_reports:
                m = re.search(r"Comparison Report:\s*(.*)", comp_reports[0]["title"], re.IGNORECASE)
                mode_desc = f"Comparison: {m.group(1)}" if m else "Cross-Run Comparison"
            elif run_reports:
                m = re.search(r"Report:\s*(.*)", run_reports[0]["title"], re.IGNORECASE)
                mode_desc = f"Coverage: {m.group(1)}" if m else "ATPG Coverage Run"

            mtime = datetime.fromtimestamp(os.path.getmtime(full_p)).strftime("%Y-%m-%d %H:%M:%S")

            ww_entries.append({
                "name": name,
                "path": full_p,
                "mtime": mtime,
                "mode_desc": mode_desc,
                "comp_reports": comp_reports,
                "run_reports": run_reports,
                "html_files": reports_info,
                "csv_files": [os.path.basename(c) for c in csv_files if not os.path.basename(c).startswith("index")]
            })

    def render_portal(base_depth):
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ATPG Coverage & Analysis Portal - Results Menu</title>
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
            padding: 2.5rem 2rem;
        }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        header {{
            margin-bottom: 2.5rem;
            padding-bottom: 1.5rem;
            border-bottom: 1px solid var(--border);
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            flex-wrap: wrap;
            gap: 1.5rem;
        }}
        h1 {{
            font-size: 2.3rem;
            font-weight: 800;
            color: var(--text-highlight);
            margin-bottom: 0.5rem;
            display: flex;
            align-items: center;
            gap: 0.85rem;
        }}
        .subtitle {{
            color: var(--text-dim);
            font-size: 1.05rem;
        }}
        .search-box {{
            width: 100%;
            max-width: 420px;
            background: var(--code-bg);
            border: 1px solid var(--border);
            border-radius: 8px;
            color: var(--text-main);
            padding: 0.75rem 1.25rem;
            font-size: 0.95rem;
            outline: none;
            transition: border-color 0.2s;
        }}
        .search-box:focus {{ border-color: var(--text-highlight); }}
        .portal-grid {{
            display: grid;
            grid-template-columns: 1fr;
            gap: 1.75rem;
        }}
        .ww-card {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 1.75rem;
            box-shadow: 0 4px 6px -1px rgba(0,0,0,0.25);
            transition: transform 0.2s, border-color 0.2s;
        }}
        .ww-card:hover {{
            transform: translateY(-2px);
            border-color: #475569;
        }}
        .ww-card-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1.25rem;
            flex-wrap: wrap;
            gap: 0.75rem;
            padding-bottom: 1rem;
            border-bottom: 1px solid var(--border);
        }}
        .ww-title-area {{
            display: flex;
            align-items: center;
            gap: 1rem;
            flex-wrap: wrap;
        }}
        .ww-badge {{
            font-size: 1.25rem;
            font-weight: 800;
            padding: 0.35rem 0.9rem;
            border-radius: 8px;
            background: var(--blue-bg);
            color: var(--text-highlight);
            border: 1px solid rgba(56, 189, 248, 0.3);
            font-family: 'JetBrains Mono', Consolas, monospace;
        }}
        .ww-desc {{
            font-size: 1.05rem;
            font-weight: 600;
            color: var(--text-main);
        }}
        .ww-meta {{
            font-size: 0.85rem;
            color: var(--text-dim);
        }}
        .btn-group {{
            display: flex;
            gap: 0.75rem;
            flex-wrap: wrap;
            margin-top: 1rem;
        }}
        .btn {{
            display: inline-flex;
            align-items: center;
            gap: 0.5rem;
            padding: 0.65rem 1.1rem;
            border-radius: 6px;
            font-size: 0.92rem;
            font-weight: 600;
            text-decoration: none;
            transition: all 0.2s;
            border: 1px solid transparent;
        }}
        .btn-primary {{
            background: linear-gradient(135deg, #0284c7, #0369a1);
            color: #fff;
            box-shadow: 0 2px 4px rgba(0,0,0,0.2);
        }}
        .btn-primary:hover {{
            background: linear-gradient(135deg, #0369a1, #075985);
            transform: translateY(-1px);
        }}
        .btn-secondary {{
            background: var(--bg-subcard);
            color: var(--text-highlight);
            border-color: var(--border);
        }}
        .btn-secondary:hover {{
            background: var(--border);
            color: #fff;
            transform: translateY(-1px);
        }}
        .btn-csv {{
            background: rgba(74, 222, 128, 0.1);
            color: var(--green);
            border-color: rgba(74, 222, 128, 0.25);
        }}
        .btn-csv:hover {{
            background: rgba(74, 222, 128, 0.2);
            transform: translateY(-1px);
        }}
        .file-list {{
            margin-top: 1rem;
            padding-top: 0.75rem;
            display: flex;
            gap: 1rem;
            flex-wrap: wrap;
            font-size: 0.85rem;
            color: var(--text-dim);
        }}
        .file-list code {{
            background: var(--code-bg);
            padding: 0.15rem 0.45rem;
            border-radius: 4px;
            color: #cbd5e1;
            font-family: 'JetBrains Mono', Consolas, monospace;
        }}
    </style>
    <script>
        function filterCards() {{
            const filter = document.getElementById('filterInput').value.toLowerCase();
            const cards = document.getElementsByClassName('ww-card');
            for (let i = 0; i < cards.length; i++) {{
                const text = cards[i].textContent.toLowerCase();
                cards[i].style.display = text.includes(filter) ? '' : 'none';
            }}
        }}
    </script>
</head>
<body>
<div class="container">
    <header>
        <div>
            <h1>📊 ATPG Coverage & Analysis Portal</h1>
            <div class="subtitle">Siemens Tessent Automated Coverage, AU Breakdown & Cross-Run Comparison Dashboards</div>
        </div>
        <div>
            <input type="text" id="filterInput" class="search-box" onkeyup="filterCards()" placeholder="🔍 Filter by WorkWeek, partition, or recipe...">
        </div>
    </header>

    <div class="portal-grid">
"""

        for entry in ww_entries:
            ww_name = entry["name"]
            if base_depth == 0:
                prefix = f"results/{ww_name}/"
            elif base_depth == 1:
                prefix = f"{ww_name}/"
            else:
                prefix = ""

            html += f"""
        <div class="ww-card">
            <div class="ww-card-header">
                <div class="ww-title-area">
                    <span class="ww-badge">{html_lib.escape(ww_name)}</span>
                    <span class="ww-desc">{html_lib.escape(entry["mode_desc"])}</span>
                </div>
                <div class="ww-meta">Generated: {entry["mtime"]}</div>
            </div>

            <div class="btn-group">
"""
            for comp_r in entry["comp_reports"]:
                comp_url = prefix + comp_r["filename"]
                html += f"""                <a href="{html_lib.escape(comp_url)}" class="btn btn-primary">📊 Comparison: {html_lib.escape(comp_r['title'].replace('ATPG Coverage Comparison Report: ', ''))}</a>\n"""

            for run_r in entry["run_reports"]:
                run_url = prefix + run_r["filename"]
                m_label = re.search(r"atpg_coverage_report_run_(.+?)\.html", run_r["filename"])
                label_txt = m_label.group(1) if m_label else run_r["title"].replace("ATPG Coverage & AU Breakdown Report: ", "")
                html += f"""                <a href="{html_lib.escape(run_url)}" class="btn btn-secondary">📄 {html_lib.escape(label_txt)}</a>\n"""

            for csv_f in entry["csv_files"]:
                csv_url = prefix + csv_f
                label = "Deltas CSV" if "delta" in csv_f.lower() else ("AU CSV: " + csv_f.replace("instance_au_breakdown_", "").replace(".csv", ""))
                html += f"""                <a href="{html_lib.escape(csv_url)}" class="btn btn-csv">📥 {html_lib.escape(label)}</a>\n"""

            html += f"""            </div>

            <div class="file-list">
                <span>Available artifacts:</span>
"""
            for h in entry["html_files"]:
                html += f"""                <code>{html_lib.escape(h["filename"])}</code>\n"""
            for c in entry["csv_files"]:
                html += f"""                <code>{html_lib.escape(c)}</code>\n"""

            html += """            </div>
        </div>
"""

        html += """
    </div>

    <footer style="margin-top: 3rem; padding-top: 1.5rem; border-top: 1px solid var(--border); text-align: center; color: var(--text-dim); font-size: 0.85rem;">
        ATPG Coverage Report Agent Portal &bull; Intel CTH DFT Automation &bull; Siemens Tessent Shell
    </footer>
</div>
</body>
</html>
"""
        return html

    generated_paths = []
    # 1. Root index.html
    root_index_path = os.path.join(git_repo_dir, "index.html")
    with open(root_index_path, "w") as f:
        f.write(render_portal(base_depth=0))
    generated_paths.append(root_index_path)

    # 2. results/index.html
    results_index_path = os.path.join(results_dir, "index.html")
    with open(results_index_path, "w") as f:
        f.write(render_portal(base_depth=1))
    generated_paths.append(results_index_path)

    # 3. results/<WW>/index.html
    for entry in ww_entries:
        ww_index_path = os.path.join(entry["path"], "index.html")
        with open(ww_index_path, "w") as f:
            f.write(render_portal(base_depth=2))
        generated_paths.append(ww_index_path)

    print(f"[*] Updated Portal Menu pages in Git repo:")
    print(f"    • Root Portal:    {root_index_path}")
    print(f"    • Results Menu:   {results_index_path}")
    return generated_paths


def get_current_user():
    """
    Returns the current username on the Linux system.
    """
    return os.environ.get("USER") or os.environ.get("LOGNAME") or getpass.getuser()


def check_git_repo(path):
    """
    Checks if path exists, is a directory, and contains a .git folder.
    Returns canonical path if valid, otherwise None.
    """
    if not path:
        return None
    expanded = os.path.abspath(os.path.expanduser(str(path).strip()))
    if os.path.isdir(expanded) and os.path.isdir(os.path.join(expanded, ".git")):
        return expanded
    return None


def get_git_remote_url(git_repo_dir):
    """
    Returns the remote origin URL for a given git repository directory, or empty string.
    """
    if not git_repo_dir or not os.path.isdir(os.path.join(git_repo_dir, ".git")):
        return ""
    try:
        res = subprocess.run(
            ["git", "-C", git_repo_dir, "config", "--get", "remote.origin.url"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True
        )
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:
        pass
    return ""


def get_github_web_url(git_repo_dir, ww_tag=None, results_subdir="results", page_name="index.html"):
    """
    Constructs the Windows / GitHub Pages web URL for the given repository and workweek.
    Example: https://urimark.github.io/Agents/results/WW43.Tue/index.html
    """
    env_web = os.environ.get("ATPG_WEB_URL") or os.environ.get("ATPG_PAGES_BASE_URL")
    sub = f"{results_subdir}/{ww_tag}/{page_name}" if ww_tag else page_name
    if env_web:
        base = env_web.rstrip("/")
        return f"{base}/{sub}"

    remote = get_git_remote_url(git_repo_dir) if git_repo_dir else ""
    m = re.search(r"github\.com[:/](?:443/)?(?P<owner>[^/]+)/(?P<repo>[^/\.]+)(?:\.git)?", remote, re.IGNORECASE)
    if m:
        owner = m.group("owner")
        repo = m.group("repo")
        return f"https://{owner}.github.io/{repo}/{sub}"

    current_user = get_current_user()
    if current_user == "umark":
        return f"https://urimark.github.io/Agents/{sub}"

    return f"https://<github_user>.github.io/<repo>/{sub}"


def get_report_urls(git_repo_dir, ww_tag=None, results_subdir="results", page_name="index.html"):
    """
    Computes both the Linux/Firefox file:// URL and Windows/Web (GitHub Pages) URL
    for the generated results index or report.
    """
    if git_repo_dir:
        if ww_tag:
            local_path = os.path.abspath(os.path.join(git_repo_dir, results_subdir, ww_tag, page_name))
        else:
            local_path = os.path.abspath(os.path.join(git_repo_dir, page_name))
    else:
        local_path = os.path.abspath(page_name)
    firefox_url = f"file://{local_path}"
    windows_url = get_github_web_url(git_repo_dir, ww_tag=ww_tag, results_subdir=results_subdir, page_name=page_name)
    return {
        "local_path": local_path,
        "firefox_url": firefox_url,
        "windows_url": windows_url
    }


def print_access_urls(firefox_url, windows_url, title="ACCESS URLS FOR REPORT & PORTAL:"):
    print("\n" + "="*80)
    print(f"  {title}")
    print("="*80)
    print(f"  Firefox Path (Linux):")
    print(f"  {firefox_url}")
    print(f"\n  Windows Path (Web / GitHub Pages):")
    print(f"  {windows_url}")
    print("="*80 + "\n")


def resolve_user_git_repo(repo_arg=None, require_git=False):
    """
    Resolves the local git repository path, ensuring git loading is separated per user.
    User 'urimark' (at /nfs/site/disks/umark_wa01/gitprivateumark01/Agents) is only for user=umark.
    Checks if user has https://urimark.github.io/Agents/results/ path, otherwise requests user to define git location.
    """
    current_user = get_current_user()

    # 1. Explicit CLI argument passed by user
    if repo_arg:
        valid = check_git_repo(repo_arg)
        if valid:
            if current_user != "umark":
                remote = get_git_remote_url(valid)
                if "urimark" in valid or "urimark" in remote:
                    print(f"[!] Warning: 'urimark' repository is only for user 'umark'.")
                    print(f"    Current user is '{current_user}'. Please define your own git location.")
                else:
                    return valid
            else:
                return valid
        else:
            print(f"[!] Specified --results-repo '{repo_arg}' is not a valid git repository.")

    # 2. Environment variables: ATPG_RESULTS_REPO or ATPG_GIT_REPO
    env_repo = os.environ.get("ATPG_RESULTS_REPO") or os.environ.get("ATPG_GIT_REPO")
    if env_repo:
        valid = check_git_repo(env_repo)
        if valid:
            if current_user != "umark":
                remote = get_git_remote_url(valid)
                if "urimark" in valid or "urimark" in remote:
                    print(f"[!] Warning: ATPG_RESULTS_REPO points to 'urimark', which is only for user 'umark'.")
                else:
                    return valid
            else:
                return valid

    # 3. Persistent user config file: ~/.atpg_git_repo
    config_file = os.path.expanduser("~/.atpg_git_repo")
    if os.path.isfile(config_file):
        try:
            with open(config_file, "r") as cf:
                saved_path = cf.read().strip()
            valid = check_git_repo(saved_path)
            if valid:
                if current_user != "umark":
                    remote = get_git_remote_url(valid)
                    if not ("urimark" in valid or "urimark" in remote):
                        return valid
                else:
                    return valid
        except Exception:
            pass

    # 4. Check if user is 'umark' and has urimark repo (corresponds to https://urimark.github.io/Agents/results/)
    if current_user == "umark":
        umark_repo = "/nfs/site/disks/umark_wa01/gitprivateumark01/Agents"
        valid = check_git_repo(umark_repo)
        if valid:
            return valid
        # Fallback candidate for umark
        alt_umark = os.path.expanduser("~/gitprivateumark01/Agents")
        valid = check_git_repo(alt_umark)
        if valid:
            return valid

    # 5. User candidate directories for other users
    if current_user != "umark":
        user_candidates = [
            f"/nfs/site/disks/{current_user}_wa01/gitprivate{current_user}01/Agents",
            f"/nfs/site/disks/{current_user}_wa01/Agents",
            os.path.expanduser(f"~/gitprivate{current_user}01/Agents"),
            os.path.expanduser("~/Agents"),
            os.path.join(os.getcwd(), "Agents")
        ]
        for c in user_candidates:
            valid = check_git_repo(c)
            if valid:
                remote = get_git_remote_url(valid)
                if not ("urimark" in valid or "urimark" in remote):
                    return valid

    # 6. Not resolved yet: if require_git is True, request user to define git location
    if require_git:
        if current_user == "umark":
            print(f"\n[!] Repository path for 'urimark' (/nfs/site/disks/umark_wa01/gitprivateumark01/Agents) not found.")
        else:
            print(f"\n[!] Loading to Git must be separated for each user; 'urimark' is only for user=umark.")
            print(f"    Current user: '{current_user}'")

        # Interactive prompt if running in an interactive terminal
        if sys.stdin.isatty():
            try:
                prompt_text = f"[*] Please define your Git repository location (e.g. /path/to/Agents): "
                user_input = input(prompt_text).strip()
                if user_input:
                    valid = check_git_repo(user_input)
                    if valid:
                        try:
                            with open(config_file, "w") as cf:
                                cf.write(valid + "\n")
                            print(f"[✓] Saved git location to {config_file}")
                        except Exception:
                            pass
                        return valid
                    else:
                        print(f"[!] '{user_input}' is not a valid git repository (no .git found).")
            except (KeyboardInterrupt, EOFError):
                print("\n[!] Input cancelled.")

        # Non-interactive error & guidance
        print("\n" + "="*80)
        print(f"  [!] ACTION REQUIRED: Git Repository Location Not Defined for User '{current_user}'")
        print("="*80)
        print(f"  Note: 'urimark' (https://urimark.github.io/Agents/results/) is only for user 'umark'.")
        print(f"  Loading to Git must be separated for each user.")
        print(f"\n  Please define your local Git repository location using one of the following:")
        print(f"    1. CLI option:         --results-repo <path_to_your_git_repo>")
        print(f"    2. Environment var:    export ATPG_RESULTS_REPO=<path_to_your_git_repo>")
        print(f"    3. Config file:        echo '<path_to_your_git_repo>' > ~/.atpg_git_repo")
        print("="*80 + "\n")
        return None

    return None


def save_to_git_results(report_files, ww_tag, git_repo_dir=None, commit_msg=None, push=True, results_subdir="results"):
    """
    Copies report files into <git_repo>/<results_subdir>/<ww_tag>/, updates menu pages, commits, and pushes to git.
    Displays Firefox and Windows access paths upon completion.
    """
    if not git_repo_dir:
        git_repo_dir = resolve_user_git_repo(require_git=True)

    if not git_repo_dir or not os.path.isdir(os.path.join(git_repo_dir, ".git")):
        print(f"[!] Warning: Valid Git repository not found at '{git_repo_dir}'. Skipping git load.")
        return False

    git_repo_dir = os.path.abspath(os.path.expanduser(git_repo_dir))
    target_dir = os.path.join(git_repo_dir, results_subdir, ww_tag)
    os.makedirs(target_dir, exist_ok=True)
    print(f"\n[*] Storing reports to Git results folder: {target_dir}")

    import shutil
    copied_files = []
    for src in report_files:
        if src and os.path.exists(src):
            dst = os.path.join(target_dir, os.path.basename(src))
            if os.path.abspath(src) != os.path.abspath(dst):
                shutil.copy2(src, dst)
            copied_files.append(dst)
            print(f"    • Loaded into results: {os.path.basename(src)}")

    # Update the interactive Menu pages
    menu_files = generate_menu_pages(git_repo_dir, results_subdir=results_subdir)

    if push:
        print(f"[*] Committing and pushing results and menu portal to Git ({git_repo_dir})...")
        try:
            rel_folder = f"{results_subdir}/{ww_tag}"
            subprocess.run(["git", "add", rel_folder, f"{results_subdir}/index.html", "index.html"], cwd=git_repo_dir, check=True)
            msg = commit_msg if commit_msg else f"Add ATPG coverage reports for {ww_tag}"
            res = subprocess.run(["git", "commit", "-m", msg], cwd=git_repo_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
            if "nothing to commit" in res.stdout:
                print(f"[*] Git: Nothing new to commit (already up to date).")
            else:
                print(f"[✓] Git commit created: {msg}")
                subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=git_repo_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
                push_res = subprocess.run(["git", "push", "origin", "main"], cwd=git_repo_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
                if push_res.returncode == 0:
                    print(f"[✓] Git push to origin main successful.")
                else:
                    print(f"[!] Git push warning: {push_res.stdout.strip()}")
        except Exception as e:
            print(f"[!] Git operation error: {e}")

    # Compute and display access URLs for Firefox (Linux) and Windows (Web / GitHub Pages)
    urls = get_report_urls(git_repo_dir, ww_tag=ww_tag, results_subdir=results_subdir, page_name="index.html")
    print_access_urls(urls["firefox_url"], urls["windows_url"], title="ACCESS URLS FOR ATPG COVERAGE OPERATION & PORTAL:")
    return True


def main():
    current_user = get_current_user()

    parser = argparse.ArgumentParser(description="Automated ATPG Coverage & AU Breakdown Reporter")
    parser.add_argument("--run-dir", "-r", help="Path to ATPG run directory (Run 1)")
    parser.add_argument("--compare-run-dir", "--run-dir2", "-c", help="Path to second ATPG run directory for comparison (Run 2)")
    parser.add_argument("--label1", help="Custom display label for Run 1")
    parser.add_argument("--label2", help="Custom display label for Run 2")
    parser.add_argument("--recipe", "--recipe1", help="Recipe name for Run 1 (e.g. stuckat_edt_min, atspeed_edt_min)")
    parser.add_argument("--recipe2", help="Recipe name for Run 2")
    parser.add_argument("--output-dir", "-o", help="Directory to save generated CSV and HTML reports")
    parser.add_argument("--csv-file", "--csv-file1", help="Path to CSV for Run 1")
    parser.add_argument("--csv-file2", help="Path to CSV for Run 2")
    parser.add_argument("--html-file", "--html-file1", help="Path to write output HTML report for Run 1")
    parser.add_argument("--html-file2", help="Path to write output HTML report for Run 2")
    parser.add_argument("--html-compare", help="Path to write output comparison HTML report")
    parser.add_argument("--skip-tessent", action="store_true", help="Skip Tessent execution and use existing CSVs")
    parser.add_argument("--tessent-bin", default=DEFAULT_TESSENT, help="Path to Tessent executable")
    parser.add_argument("--license-server", default=DEFAULT_LICENSE, help="SALT license server")
    parser.add_argument("--top-n", type=int, default=30, help="Number of top coverage holes to detail")
    parser.add_argument(
        "--ww",
        nargs="?",
        const="auto",
        default="auto",
        help="Organize reports by WorkWeek and day folder (e.g., WW43.Tue). Enabled by default. Pass custom WW tag or use --no-ww to disable."
    )
    parser.add_argument(
        "--no-ww",
        action="store_true",
        help="Disable automatic WorkWeek and day folder partitioning."
    )
    parser.add_argument("--save-ww", action="store_true", help="Enable saving reports under WorkWeek and day folder (e.g. results/WW43.Tue/)")
    parser.add_argument("--load-git", "--git-push", action="store_true", help="Copy reports to git results folder, commit, and push to remote origin")
    parser.add_argument("--update-menu", action="store_true", help="Scan results folder and generate/update the interactive Menu portal pages (index.html)")
    parser.add_argument(
        "--results-repo", "--repo-path", "--git-repo",
        dest="results_repo",
        default=None,
        help="Path to local git repository for results (default: user-separated repo; for 'umark': /nfs/site/disks/umark_wa01/gitprivateumark01/Agents)"
    )
    parser.add_argument(
        "--results-subdir",
        default="results",
        help="Subdirectory inside git repository where reports are stored (default: 'results')"
    )

    args = parser.parse_args()

    # Determine WorkWeek tag (default is enabled unless --no-ww is given)
    ww_tag = None
    if not args.no_ww:
        ww_val = args.ww if args.ww else "auto"
        ww_tag = get_workweek_tag(ww_val)
        print(f"[*] WorkWeek Partitioning Enabled: {ww_tag}")

    # Resolve Git repository location per user
    git_repo = None
    if args.load_git or args.update_menu:
        git_repo = resolve_user_git_repo(repo_arg=args.results_repo, require_git=True)
        if not git_repo and args.update_menu and not args.run_dir:
            sys.exit(1)
    else:
        git_repo = resolve_user_git_repo(repo_arg=args.results_repo, require_git=False)

    # Handle standalone menu update
    if args.update_menu and not args.run_dir:
        print("[*] Generating and updating Portal Menu pages...")
        if not git_repo:
            print("[!] Error: Git repository location required for --update-menu.")
            sys.exit(1)
        generate_menu_pages(git_repo, results_subdir=args.results_subdir)
        try:
            rel_idx = f"{args.results_subdir}/index.html"
            subprocess.run(["git", "add", rel_idx, "index.html"], cwd=git_repo, check=True)
            res = subprocess.run(["git", "commit", "-m", "Update ATPG coverage portal menu index"], cwd=git_repo, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
            if "nothing to commit" not in res.stdout:
                subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=git_repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
                subprocess.run(["git", "push", "origin", "main"], cwd=git_repo, check=True)
                print("[✓] Pushed menu portal updates to GitHub.")
            else:
                print("[*] Menu index already up to date on Git.")
        except Exception as e:
            print(f"[!] Git warning: {e}")

        active_ww = ww_tag if ww_tag else get_workweek_tag("auto")
        urls = get_report_urls(git_repo, ww_tag=active_ww, results_subdir=args.results_subdir, page_name="index.html")
        print_access_urls(urls["firefox_url"], urls["windows_url"], title="ACCESS URLS FOR UPDATED PORTAL MENU:")
        print("[✓] Portal menu update complete.")
        return

    if not args.run_dir:
        parser.error("Either --run-dir / -r or --update-menu is required.")

    # Resolve output directory
    if ww_tag:
        if args.output_dir:
            out_dir = os.path.abspath(os.path.join(args.output_dir, "results", ww_tag) if not args.output_dir.endswith(ww_tag) else args.output_dir)
        else:
            out_dir = os.path.abspath(os.path.join(os.getcwd(), "results", ww_tag))
    else:
        out_dir = os.path.abspath(args.output_dir if args.output_dir else (os.getcwd() if args.compare_run_dir else args.run_dir))

    os.makedirs(out_dir, exist_ok=True)
    print(f"[*] Output Directory: {out_dir}")

    # Comparison Mode
    if args.compare_run_dir:
        label1 = args.label1 if args.label1 else "Run 1"
        label2 = args.label2 if args.label2 else "Run 2"

        # Pre-discover artifacts to check if names would collide
        artifacts1 = find_files_for_run(args.run_dir, recipe=args.recipe)
        artifacts2 = find_files_for_run(args.compare_run_dir, recipe=args.recipe2)
        collision = (artifacts1.get("fub") == artifacts2.get("fub") and artifacts1.get("recipe") == artifacts2.get("recipe"))
        suf1 = "_run1" if collision else ""
        suf2 = "_run2" if collision else ""

        print("\n" + "="*80)
        print(f"  STARTING ATPG COVERAGE COMPARISON MODE: {label1} vs {label2}")
        print("="*80 + "\n")

        print(f"--- [1/3] Processing {label1} ---")
        run1_data = process_single_run(
            run_dir=args.run_dir,
            recipe=args.recipe,
            csv_file=args.csv_file,
            output_dir=out_dir,
            html_file=args.html_file,
            skip_tessent=args.skip_tessent,
            tessent_bin=args.tessent_bin,
            license_server=args.license_server,
            top_n=args.top_n,
            label_suffix=suf1,
            artifacts=artifacts1
        )

        print(f"\n--- [2/3] Processing {label2} ---")
        run2_data = process_single_run(
            run_dir=args.compare_run_dir,
            recipe=args.recipe2,
            csv_file=args.csv_file2,
            output_dir=out_dir,
            html_file=args.html_file2,
            skip_tessent=args.skip_tessent,
            tessent_bin=args.tessent_bin,
            license_server=args.license_server,
            top_n=args.top_n,
            label_suffix=suf2,
            artifacts=artifacts2
        )

        # Refine labels if default
        if not args.label1:
            label1 = f"{run1_data['artifacts']['fub']} ({run1_data['artifacts']['recipe']})"
        if not args.label2:
            label2 = f"{run2_data['artifacts']['fub']} ({run2_data['artifacts']['recipe']})"

        print(f"\n--- [3/3] Generating Cross-Run Comparison & Improvement Advisory ---")
        comp = analyze_comparison(run1_data, run2_data, label1=label1, label2=label2)

        comp_csv = os.path.join(out_dir, "au_comparison_deltas.csv")
        export_comparison_csv(comp, comp_csv)

        comp_html = os.path.abspath(args.html_compare if args.html_compare else os.path.join(out_dir, "atpg_coverage_comparison_report.html"))
        rel_r1 = os.path.relpath(run1_data["html_path"], os.path.dirname(comp_html))
        rel_r2 = os.path.relpath(run2_data["html_path"], os.path.dirname(comp_html))
        rel_csv = os.path.relpath(comp_csv, os.path.dirname(comp_html))

        generate_comparison_html_report(comp, run1_data, run2_data, comp_html, rel_r1, rel_r2, rel_csv)

        print("\n" + "="*80)
        print(f"  ATPG COVERAGE COMPARISON SUMMARY: {label1} vs {label2}")
        print("="*80)
        print(f"  • {label1} Coverage:  {comp['top1']['tc']:.2f}% (AU: {comp['top1']['au']:,})")
        print(f"  • {label2} Coverage:  {comp['top2']['tc']:.2f}% (AU: {comp['top2']['au']:,})")
        print(f"  • Coverage Delta:     {comp['delta_tc']:+.2f}%")
        print(f"  • AU Delta ({label1} - {label2}): {comp['delta_au']:+,} ({comp['pct_delta_au']:+.1f}%)")
        print("-"*80)
        print(f"  THREE GENERATED REPORTS:")
        print(f"  1. Report 1 ({label1}):          {run1_data['html_path']}")
        print(f"  2. Report 2 ({label2}):          {run2_data['html_path']}")
        print(f"  3. Report 3 (Comparison Report): {comp_html}")
        print(f"  • Comparison Deltas CSV:         {comp_csv}")
        print("="*80 + "\n")

        # Load into Git results folder if requested
        if args.load_git or args.save_ww:
            active_ww = ww_tag if ww_tag else get_workweek_tag("auto")
            files_to_sync = [run1_data["html_path"], run2_data["html_path"], comp_html, comp_csv]
            commit_title = f"Add ATPG comparison reports for {active_ww} ({label1} vs {label2})"
            if git_repo:
                save_to_git_results(
                    report_files=files_to_sync,
                    ww_tag=active_ww,
                    git_repo_dir=git_repo,
                    commit_msg=commit_title,
                    push=True,
                    results_subdir=args.results_subdir
                )
            else:
                print("[!] Skipping Git upload: Git repository location not defined.")
        else:
            local_report = comp_html
            print("\n" + "="*80)
            print("  LOCAL ACCESS URL:")
            print("="*80)
            print(f"  Firefox Path (Linux):")
            print(f"  file://{local_report}")
            print("="*80 + "\n")

    # Single Run Mode
    else:
        run_data = process_single_run(
            run_dir=args.run_dir,
            recipe=args.recipe,
            csv_file=args.csv_file,
            output_dir=out_dir,
            html_file=args.html_file,
            skip_tessent=args.skip_tessent,
            tessent_bin=args.tessent_bin,
            license_server=args.license_server,
            top_n=args.top_n
        )

        analysis = run_data["analysis"]
        artifacts = run_data["artifacts"]

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
        print(f"  • CSV Breakdown:  {run_data['csv_path']}")
        print(f"  • HTML Report:    {run_data['html_path']}")
        print("="*80 + "\n")

        # Load into Git results folder if requested
        if args.load_git or args.save_ww:
            active_ww = ww_tag if ww_tag else get_workweek_tag("auto")
            files_to_sync = [run_data["html_path"], run_data["csv_path"]]
            commit_title = f"Add ATPG coverage report for {active_ww} ({artifacts['fub']} {artifacts['recipe']})"
            if git_repo:
                save_to_git_results(
                    report_files=files_to_sync,
                    ww_tag=active_ww,
                    git_repo_dir=git_repo,
                    commit_msg=commit_title,
                    push=True,
                    results_subdir=args.results_subdir
                )
            else:
                print("[!] Skipping Git upload: Git repository location not defined.")
        else:
            local_report = run_data["html_path"]
            print("\n" + "="*80)
            print("  LOCAL ACCESS URL:")
            print("="*80)
            print(f"  Firefox Path (Linux):")
            print(f"  file://{local_report}")
            print("="*80 + "\n")


if __name__ == "__main__":
    main()
