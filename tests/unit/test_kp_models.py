# tests/unit/test_kp_models.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base, CommercialProposal, CompanyProfile


def test_commercial_proposal_and_kp_fields_create():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    cp = CommercialProposal(user_id=1, number="ИПХИС-26064", vat_mode="none",
                            delivery_time="15 к.д.", items=[], total=0)
    s.add(cp); s.commit()
    assert cp.id is not None
    # новые поля профиля существуют
    prof = CompanyProfile(user_id=1, kp_number_prefix="ИПХИС", kp_counter=26063)
    s.add(prof); s.commit()
    assert prof.kp_counter == 26063
