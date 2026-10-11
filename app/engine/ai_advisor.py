"""Intelligent file inspector using local heuristics and optional Gemini AI analysis."""
from __future__ import annotations
import os
from pathlib import Path
from typing import Dict, Optional
from pydantic import BaseModel

from app.config import ARCHIVE_EXTENSIONS
from app.engine.archives import inspect_archive
from app.engine.classifier import _in_build_output_folder, _in_disposable_folder
from app.engine.installers import inspect_exe, is_uninstaller_name


class AIAnalysisResult(BaseModel):
    file_name: str
    file_path: str
    detected_type: str
    origin_application: str
    safety_verdict: str  # "Safe to Delete", "Review Carefully", "Keep File"
    explanation: str
    recommendation: str
    ai_powered: bool = False


# Known extension intelligence database for instant offline analysis
OFFLINE_KNOWLEDGE_BASE: Dict[str, Dict[str, str]] = {
    ".iso": {
        "type": "Operating System / Disc Image (ISO)",
        "origin": "OS Installation Media (Windows/Linux) or Optical Disc Backup",
        "verdict": "Review Carefully",
        "explanation": "This is an optical disc or operating system installation image. It can also be a backup of a disc you no longer have.",
        "recommendation": "If it is installation media you can download again, it can go to reclaim several gigabytes."
    },
    ".img": {
        "type": "Raw Disk Image",
        "origin": "OS Installer / Raspberry Pi / Virtual Machine Disk",
        "verdict": "Review Carefully",
        "explanation": "A raw sector-by-sector disk image, used for flashing OS images but also for backing up SD cards and drives.",
        "recommendation": "Delete it only if it is an OS image you can download again, not a backup."
    },
    ".vhd": {
        "type": "Virtual Hard Disk Image",
        "origin": "Hyper-V / VirtualBox / Windows Backup",
        "verdict": "Review Carefully",
        "explanation": "Virtual hard drive file used by virtual machines or system image backups.",
        "recommendation": "If this VM is no longer needed or this is an abandoned test image, delete it to recover huge storage."
    },
    ".zip": {
        "type": "Compressed Setup / Download Archive",
        "origin": "Downloaded Software / Archive Package",
        "verdict": "Safe to Delete",
        "explanation": "Compressed archive often used to distribute portable applications, setup installers, or driver packs.",
        "recommendation": "If you have already extracted or installed the application, the ZIP file in Downloads is safe to delete."
    },
    ".7z": {
        "type": "7-Zip High Compression Archive",
        "origin": "7-Zip / Downloaded Software Setup",
        "verdict": "Safe to Delete",
        "explanation": "Compressed archive commonly used for distributing large games, software tools, or SDK packages.",
        "recommendation": "Safe to delete after extracting contents."
    },
    ".apk": {
        "type": "Android Application Package",
        "origin": "Android OS / Mobile App Installers",
        "verdict": "Safe to Delete",
        "explanation": "This is an installation package for an Android mobile device. On a Windows PC, APK files are usually downloaded backups or emulator installers.",
        "recommendation": "Unless you plan to sideload this APK onto an Android device or emulator, you can safely delete it."
    },
    ".class": {
        "type": "Compiled Java Bytecode",
        "origin": "Java Development Kit (javac) / Eclipse / IntelliJ / NetBeans",
        "verdict": "Safe to Delete",
        "explanation": "This is a compiled Java binary produced during source code compilation. It is completely reproducible from the original .java source code.",
        "recommendation": "Safe to delete. Your IDE or build tool (Maven/Gradle) will recompile it automatically when needed."
    },
    ".jar": {
        "type": "Java Archive",
        "origin": "Java Runtime Environment / Maven / Gradle",
        "verdict": "Review Carefully",
        "explanation": "This is a packaged Java library or executable application. If found in a temporary, build, or Downloads folder, it is likely an old version or build artifact.",
        "recommendation": "If you don't run this program directly and it was downloaded a long time ago, it is safe to remove."
    },
    ".msi": {
        "type": "Windows Installer Package",
        "origin": "Windows Installer Service",
        "verdict": "Safe to Delete",
        "explanation": "This is a setup installer package. Once the software has been installed on your PC, the installer file in your Downloads folder is no longer required.",
        "recommendation": "Safe to delete from Downloads/Desktop. Does not uninstall the application."
    },
    ".exe": {
        "type": "Windows Executable Program",
        "origin": "Software Application / Setup Wizard",
        "verdict": "Review Carefully",
        "explanation": "This is an executable binary. If the name includes 'setup', 'installer', or it sits in your Downloads folder, it is likely a leftover installer.",
        "recommendation": "If it is an installer for software you already have installed, delete it to recover space. If it is a portable app you use, keep it."
    },
    ".crdownload": {
        "type": "Incomplete Google Chrome / Chromium Download",
        "origin": "Google Chrome / Brave / Edge Browser",
        "verdict": "Safe to Delete",
        "explanation": "This is an interrupted or cancelled browser download that was never finished.",
        "recommendation": "Completely safe to delete; the file is unusable in its current truncated state."
    },
    ".part": {
        "type": "Incomplete Firefox Download",
        "origin": "Mozilla Firefox",
        "verdict": "Safe to Delete",
        "explanation": "This is a partial file from an interrupted download session.",
        "recommendation": "Completely safe to delete."
    },
    ".tmp": {
        "type": "Temporary Data File",
        "origin": "Windows OS / Active Applications",
        "verdict": "Safe to Delete",
        "explanation": "Temporary scratchpad file created by an application and not cleaned up upon exit.",
        "recommendation": "Safe to delete."
    },
    ".dmp": {
        "type": "Memory Crash Dump",
        "origin": "Windows Error Reporting / Crashing Software",
        "verdict": "Safe to Delete",
        "explanation": "Crash dump snapshot generated when a program abruptly terminated.",
        "recommendation": "Safe to delete. Only useful for software developers debugging crash logs."
    }
}


def analyze_item(path_str: str) -> AIAnalysisResult:
    """Analyze a file or folder using Gemini if configured, or offline heuristic database."""
    path = Path(path_str)
    file_name = path.name
    suffix = path.suffix.lower()

    # Check for Gemini API Key in environment
    api_key = os.environ.get("GEMINI_API_KEY")

    if api_key:
        try:
            from google import genai
            client = genai.Client(api_key=api_key)

            stat_info = ""
            if path.exists():
                stat = path.stat()
                stat_info = f"Size: {stat.st_size} bytes, Modified: {stat.st_mtime}"

            prompt = f"""
            Analyze the following file found on a user's Windows computer during a disk cleanup scan:
            File Name: {file_name}
            Full Path: {path_str}
            {stat_info}

            Provide an analysis in this exact JSON structure:
            {{
                "detected_type": "Short name of file type",
                "origin_application": "Likely creator or software",
                "safety_verdict": "Safe to Delete" OR "Review Carefully" OR "Keep File",
                "explanation": "1-2 sentences explaining what this file is",
                "recommendation": "Clear recommendation on whether to delete or keep"
            }}
            Return only valid JSON.
            """

            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )

            import json
            cleaned = response.text.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]

            data = json.loads(cleaned.strip())
            return AIAnalysisResult(
                file_name=file_name,
                file_path=path_str,
                detected_type=data.get("detected_type", "Unknown"),
                origin_application=data.get("origin_application", "Unknown"),
                safety_verdict=data.get("safety_verdict", "Review Carefully"),
                explanation=data.get("explanation", ""),
                recommendation=data.get("recommendation", ""),
                ai_powered=True
            )
        except Exception:
            # Fall back to offline knowledge base if API fails or lacks internet
            pass

    # Offline Heuristic Analysis
    info = OFFLINE_KNOWLEDGE_BASE.get(suffix)

    # Check directory names. Only __pycache__ is safe by name; a folder called target or build
    # can hold anything (the scanner rates them Review too)
    if path.is_dir() and file_name.lower() in {"node_modules", "target", "build", "__pycache__", ".gradle"}:
        safe = file_name.lower() == "__pycache__"
        return AIAnalysisResult(
            file_name=file_name,
            file_path=path_str,
            detected_type="Software Project Dependency / Build Directory",
            origin_application="Node.js (npm), Maven, Gradle, or Python",
            safety_verdict="Safe to Delete" if safe else "Review Carefully",
            explanation=f"A folder named {file_name} usually holds package dependencies or build output, but the name alone does not prove it.",
            recommendation="Safe to delete; Python rebuilds it automatically." if safe else
                "Check it belongs to a project you build yourself. If so it can be restored with 'npm install' or re-building.",
            ai_powered=False
        )

    if suffix == ".exe" and path.is_file():
        exe = inspect_exe(path_str)
        if exe.is_uninstaller or is_uninstaller_name(file_name):
            verdict, kind = "Keep File", "Uninstaller"
            explanation = "This program removes an installed app. Windows needs it to uninstall that app cleanly."
            recommendation = "Keep it. Uninstall the app from Settings instead if you no longer want it."
        elif exe.is_installer:
            verdict, kind = "Safe to Delete", "Setup Program"
            explanation = f"JunkZero checked inside: {exe.evidence}. It installs software and is not needed to run it."
            recommendation = "If the program is already installed, this setup file can go. Keep it only if you may reinstall offline."
        else:
            verdict, kind = "Review Carefully", "Program (not an installer)"
            explanation = "JunkZero found no installer fingerprint inside. It looks like a program, tool or game you run directly."
            recommendation = "Keep it if you still use it. JunkZero does not list programs like this as junk."
        return AIAnalysisResult(
            file_name=file_name,
            file_path=path_str,
            detected_type=kind,
            origin_application="Windows program",
            safety_verdict=verdict,
            explanation=explanation,
            recommendation=recommendation,
            ai_powered=False
        )

    if suffix in ARCHIVE_EXTENSIONS and path.is_file():
        verdict = inspect_archive(path_str, path.stat().st_size)
        if verdict.is_setup:
            return AIAnalysisResult(
                file_name=file_name,
                file_path=path_str,
                detected_type="Setup / Software Archive",
                origin_application="Downloaded software package",
                safety_verdict="Safe to Delete" if verdict.clean else "Review Carefully",
                explanation=f"JunkZero looked inside without unpacking it: {verdict.summary}.",
                recommendation="If the program is already installed or extracted, this download can go.",
                ai_powered=False
            )
        if verdict.readable:
            explanation = f"JunkZero looked inside without unpacking it: {verdict.summary}."
        else:
            explanation = f"JunkZero could not look inside this archive ({verdict.summary})."
        return AIAnalysisResult(
            file_name=file_name,
            file_path=path_str,
            detected_type="Compressed Archive",
            origin_application="User archive or download",
            safety_verdict="Review Carefully",
            explanation=explanation,
            recommendation="Archives often hold personal files. Open it and check before deleting.",
            ai_powered=False
        )

    if info:
        verdict, recommendation = info["verdict"], info["recommendation"]
        # No more confident than the scanner: temp files, dumps and compiled classes are only
        # junk in the places the scanner lists them
        unsure = (suffix in {".tmp", ".dmp"} and not _in_disposable_folder(path_str)) \
            or (suffix == ".class" and not _in_build_output_folder(path_str))
        if unsure and verdict == "Safe to Delete":
            verdict = "Review Carefully"
            recommendation = "Check what it is before deleting; here it could be someone's own file."
        return AIAnalysisResult(
            file_name=file_name,
            file_path=path_str,
            detected_type=info["type"],
            origin_application=info["origin"],
            safety_verdict=verdict,
            explanation=info["explanation"],
            recommendation=recommendation,
            ai_powered=False
        )

    return AIAnalysisResult(
        file_name=file_name,
        file_path=path_str,
        detected_type=f"{suffix.upper()} file" if suffix else "Generic file",
        origin_application="User / Application storage",
        safety_verdict="Review Carefully",
        explanation=f"File with extension '{suffix}' located in {path.parent.name}.",
        recommendation="Inspect the file or open its containing folder in File Explorer before deciding to delete.",
        ai_powered=False
    )
