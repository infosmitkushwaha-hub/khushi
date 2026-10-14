from app.core import guard


def test_clean_comment_passes():
    text = ("Interested in the Meta Ads Assistant Intern role. I'm from Ranchi and build with Next.js and React. "
            "Available immediately. Portfolio: https://sumit-my-portfolio-rho.vercel.app")
    assert guard.check(text, public=True) == []


def test_degree_claims_are_blocked():
    assert any("degree" in i for i in guard.check("I'm a B.Tech graduate from Ranchi", public=False))


def test_only_the_true_years_of_experience_pass():
    assert guard.check("about 3 years of freelance work", public=True) == []
    assert any("5 years" in i for i in guard.check("I have 5 years of experience", public=True))


def test_phone_number_blocked_in_public_but_allowed_in_dm():
    assert any("phone" in i for i in guard.check("Call me on +91 98765 43210", public=True))
    assert guard.check("My number is +91 9876543210", public=False) == []


def test_email_is_never_allowed():
    assert any("email" in i for i in guard.check("Mail me at someone@gmail.com", public=False))


def test_pay_figures_only_when_the_post_stated_them():
    assert any("pay" in i for i in guard.check("Happy with ₹20,000 per month", public=False))
    post = "Salary ₹20,000 per month, Ranchi"
    assert guard.check("Is the ₹20,000 per month still on offer?", public=False, source_text=post) == []


def test_invented_results_are_blocked():
    assert any("result" in i for i in guard.check("I grew sales 300% for clients", public=True))


def test_only_portfolio_and_linkedin_links():
    assert any("link" in i for i in guard.check("See https://example.com/work", public=True))
    assert guard.check("See sumit-my-portfolio-rho.vercel.app and linkedin.com/in/x", public=True) == []
