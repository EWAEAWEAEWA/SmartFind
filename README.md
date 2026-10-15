# SmartFind
SmartFind - Intelligent Lost-and-Found Matching System
# SmartFind: Intelligent Lost-and-Found System

## Project Overview
SmartFind is a campus lost-and-found system designed to help students find lost belongings and report items they have found. It aims to make matching lost and found items easier through an intelligent matching system and AI-based object detection.

## Intended Features
- **Lost-and-Found Reports:** Store and manage reports of lost and found items.
- **Intelligent Matching:** Compare item details to identify possible matches between lost and found reports.
- **AI Object Detection:** Use a trained YOLO model (`best.pt`) to detect school-related objects in images.
- **Data Storage:** Use a CSV file (`data/reports.csv`) for sample report data.
- **Testing:** Use `test_smartfind.py` to test parts of the system.

## Current Project Status
The Python source files, sample CSV data, and trained model file (`models/best.pt`) have been added to the project.

**Important:** The AI model has not yet been tested in the PyCharm application. The complete system and its features still need to be tested to confirm that everything works as intended.

## Project Files
- `main.py` — Main program entry point.
- `smartfind_core.py` — Core system and matching logic.
- `smartfind_vision.py` — Intended image-processing and object-detection functions.
- `test_smartfind.py` — Tests for the system.
- `data/reports.csv` — Sample lost-and-found report data.
- `models/best.pt` — Trained object-detection model.
- `requirements.txt` — Python dependencies.

## Goal
Develop and test a working intelligent lost-and-found system that helps users identify possible matches for missing belongings.

## Team Collaboration
This repository is used by the project team to share code, test features, fix bugs, and improve SmartFind together.
