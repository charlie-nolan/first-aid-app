from plb.web import page_sections

HTML = """<html><head><title>Extreme Heat | Ready.gov</title></head><body>
<nav><a href="/">Home</a></nav>
<main><h1>Extreme Heat</h1>
<a class="fema-link" href="#before"><p>Before Extreme Heat</p></a>
<p>If you are a disaster survivor, please visit FEMA.gov for help.</p>
<p>Extreme heat is a period of <a href="/x">high heat</a> and humidity.</p>
<h2>Before Extreme Heat</h2>
<div class="field__label visually-hidden">Image</div><img src="a.png">
<ul><li>Learn to <a href="#illness">recognize the signs</a> of heat illnesses.</li>
<li>Drink plenty of fluids.</li></ul>
<p><strong>Know Your Risk<br></strong>Know your area’s risk.</p>
<h2>Related Content</h2><p>Junk</p></main></body></html>"""


def test_web_page_sections_are_verbatim_and_clean():
    secs = page_sections(HTML, "READY", 2)
    assert [s["para"] for s in secs] == ["2.h1", "2.h2"]
    assert secs[0]["chapter"] == "Extreme Heat"
    assert secs[0]["text"] == "Extreme heat is a period of high heat and humidity."
    assert secs[1]["heading"] == "Before Extreme Heat"
    assert secs[1]["text"] == ("- Learn to recognize the signs of heat illnesses.\n"
                               "- Drink plenty of fluids.\nKnow Your Risk\nKnow your area's risk.")
