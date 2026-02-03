"""Test citation validation logic."""

import re


def _extract_author_surnames(authors: str) -> list[str]:
    """Extract ALL author surnames from various formats."""
    if not authors:
        return []
    surnames = []
    if ";" in authors:
        author_list = [a.strip() for a in authors.split(";")]
    elif " and " in authors:
        author_list = [a.strip() for a in authors.split(" and ")]
    elif " And " in authors:
        author_list = [a.strip() for a in authors.split(" And ")]
    elif ", " in authors and " " in authors.split(",")[0]:
        author_list = [a.strip() for a in authors.split(",")]
    else:
        author_list = [authors.strip()]
    for author in author_list:
        if not author:
            continue
        if "," in author:
            surname = author.split(",")[0].strip().lower()
        else:
            parts = author.split()
            surname = parts[-1].lower() if parts else ""
        if surname:
            surnames.append(surname)
    return surnames


def test_author_extraction():
    """Test multi-author surname extraction."""
    test_cases = [
        ("Smith, John", ["smith"]),
        ("Smith, John; Jones, Mary", ["smith", "jones"]),
        ("John Smith", ["smith"]),
        ("John Smith, Mary Jones", ["smith", "jones"]),
        ("Galor, Oded and Moav, Omer", ["galor", "moav"]),
        ("John Smith and Mary Jones", ["smith", "jones"]),
        ("Smith, John; Jones, Mary; Brown, Bob", ["smith", "jones", "brown"]),
        ("Zhang Wei", ["wei"]),
        ("", []),
    ]

    print("Testing author surname extraction:")
    print("-" * 60)
    all_passed = True
    for authors, expected in test_cases:
        result = _extract_author_surnames(authors)
        status = "✓" if result == expected else "✗"
        if result != expected:
            all_passed = False
        print(f"{status} '{authors}' -> {result} (expected: {expected})")

    return all_passed


def test_citation_validation():
    """Test citation validation with various cases including multi-author papers."""

    def validate(response: str, seen_papers: dict) -> tuple[str, bool, list]:
        """Simplified validation logic."""
        if not response or not seen_papers:
            return response, False, []

        known_citations = set()
        for paper_id, info in seen_papers.items():
            authors = info.get("authors", "")
            year = info.get("year")
            if authors and year:
                for surname in _extract_author_surnames(authors):
                    known_citations.add((surname, str(year)))

        if not known_citations:
            return response, False, []

        pattern1 = r'\b([A-Z][a-z]+(?:\s+et\s+al\.?)?)\s*\((\d{4})\)'
        pattern2 = r'\(([A-Z][a-z]+(?:\s+et\s+al\.?)?),?\s*(\d{4})\)'
        pattern3 = r'\b([A-Z][a-z]+)\s+and\s+[A-Z][a-z]+\s*\((\d{4})\)'

        found_citations = []
        for pattern in [pattern1, pattern2, pattern3]:
            matches = re.findall(pattern, response)
            for match in matches:
                author = match[0].replace(" et al.", "").replace(" et al", "").strip().lower()
                year = match[1]
                found_citations.append((author, year))

        if not found_citations:
            return response, False, []

        unverified = []
        for author, year in found_citations:
            if (author, year) not in known_citations:
                unverified.append(f"{author.title()} ({year})")

        unverified = list(dict.fromkeys(unverified))
        return response, len(unverified) > 0, unverified

    # Test papers
    seen_papers = {
        "10.1234/paper1": {"authors": "Smith, John", "year": 2020},
        "10.1234/paper2": {"authors": "Galor, Oded and Moav, Omer", "year": 2000},
        "10.1234/paper3": {"authors": "Mary Johnson; Bob Wilson", "year": 2021},
    }

    print("\n\nTesting citation validation:")
    print("-" * 60)

    print("\nKnown papers:")
    for pid, info in seen_papers.items():
        surnames = _extract_author_surnames(info["authors"])
        print(f"  {info['authors']} ({info['year']}) -> surnames: {surnames}")

    test_cases = [
        ("Smith (2020) found this.", False, "Single author"),
        ("Galor and Moav (2000) showed this.", False, "Two authors with 'and'"),
        ("According to Galor (2000), this is true.", False, "First author only"),
        ("Moav (2000) extended this.", False, "Second author only"),
        ("Johnson (2021) and Wilson (2021) both found.", False, "Both authors separate"),
        ("Fake (2022) is made up.", True, "Hallucinated citation"),
        ("Smith (2020) and Fake (2019) mixed.", True, "Mix valid and hallucinated"),
        ("No citations here.", False, "No citations"),
    ]

    print("\nTest cases:")
    all_passed = True
    for response, should_warn, description in test_cases:
        _, has_warning, unverified = validate(response, seen_papers)
        passed = has_warning == should_warn
        status = "✓" if passed else "✗"
        if not passed:
            all_passed = False
        print(f"{status} {description}")
        if not passed:
            print(f"    Response: '{response}'")
            print(f"    Expected warning: {should_warn}, Got: {has_warning}")
            if unverified:
                print(f"    Unverified: {unverified}")

    return all_passed


if __name__ == "__main__":
    print("=" * 60)
    print("CITATION VALIDATION TEST")
    print("=" * 60)
    print()

    passed1 = test_author_extraction()
    passed2 = test_citation_validation()

    print("\n" + "=" * 60)
    if passed1 and passed2:
        print("ALL TESTS PASSED ✓")
    else:
        print("SOME TESTS FAILED ✗")
    print("=" * 60)
