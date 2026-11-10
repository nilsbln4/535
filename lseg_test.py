import os
import lseg.data as ld
from lseg.data.session import platform

session = platform.Definition(
    app_key=os.environ["LSEG_APP_KEY"],
    grant=platform.GrantPassword(
        username=os.environ["LSEG_USERNAME"],
        password=os.environ["LSEG_PASSWORD"],
    ),
).get_session()
session.open()
ld.session.set_default(session)
try:
    df = ld.get_history("BTC=", fields=["BID", "ASK"], interval="daily", count=5)
    # Repo is public and logs are public: report that the pull worked, not the data.
    print(f"LSEG OK: {len(df)} daily rows, latest {df.index[-1].date()}")
finally:
    session.close()
