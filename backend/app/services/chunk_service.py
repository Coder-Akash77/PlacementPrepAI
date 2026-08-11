def split_text(text, chunk_size=500, overlap=100):

    section_keywords = {
        "EDUCATION": "Education",
        "TECHNICAL SKILLS AND INTERESTS": "Skills",
        "TECHNICAL SKILLS": "Skills",
        "PERSONAL PROJECTS": "Projects",
        "PROJECTS": "Projects",
        "WORK EXPERIENCE": "Experience",
        "EXPERIENCE": "Experience",
        "SUMMARY": "Summary"
    }

    sections = []
    current_section = "General"
    current_text = ""

    for line in text.splitlines():

        line_clean = line.strip()
        line_upper = line_clean.upper()

        # Only treat the line as a heading if it matches exactly
        detected_section = section_keywords.get(line_upper)

        if detected_section:

            if current_text.strip():
                sections.append(
                    (current_section, current_text)
                )

            current_section = detected_section
            current_text = ""

        else:
            current_text += line_clean + "\n"

    # Add final section
    if current_text.strip():
        sections.append(
            (current_section, current_text)
        )

    # Create chunks inside each section
    chunks = []
    chunk_sections = []

    for section_name, section_text in sections:

        start = 0

        while start < len(section_text):

            end = start + chunk_size

            chunk = section_text[start:end]

            if chunk.strip():
                chunks.append(chunk)
                chunk_sections.append(section_name)

            start = end - overlap

    return chunks, chunk_sections