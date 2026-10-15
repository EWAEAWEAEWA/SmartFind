# SmartFind — Project Development Prompt

## Project Goal
Develop SmartFind, an intelligent campus lost-and-found system that helps students find missing belongings by matching lost-item reports with found-item reports.

## What Needs to Be Developed
1. **Lost-and-Found Reporting** — Allow users to record items they have lost or found, including the item name, category, description, color, location, and date.
2. **Intelligent Matching Agent** — Compare lost and found reports using their details and generate possible matches ranked by similarity.
3. **AI Object Detection** — Integrate the `models/best.pt` YOLO model to detect school-related objects in uploaded images. The model is currently in the project folder but has not yet been tested in the application.
4. **Database and Data Management** — Organize reports and matching results so the system can store, retrieve, and update item records.
5. **User Interface** — Provide a simple interface for submitting reports, viewing possible matches, and checking item details.
6. **Testing and Integration** — Test each component, connect the matching agent and object-detection model, fix errors, and verify that the complete system works.

## Current Status
The project has Python source files, sample report data, a requirements file, and the `best.pt` model. The model integration and overall system functionality still need to be verified.

## Expected Result
A working prototype that accepts lost-and-found reports, identifies potential matches, and uses AI object detection to assist with identifying items in images.

## Development Guidelines
- Review the existing code before adding or rewriting features.
- Reuse working code and avoid duplicating existing functionality.
- Test changes before merging them into the main branch.
- Document setup instructions, dependencies, known issues, and completed features.
- Do not mark a feature as complete until it has been tested.
