import pytest
from app.domain import redistribute, merge_remote

def row(i,share=0,pinned=False,removed=False):return dict(offer_id=i,share=share,pinned=pinned,removed=removed)

def test_equal_integer_shares_and_input_unchanged():
    items=[row(1),row(2),row(3)];out=redistribute(items)
    assert [x['share'] for x in out]==[34,33,33]
    assert items[0]['share']==0

def test_pin_add_remove_restore():
    items=[row(1,25,True),row(2),row(3)]
    assert [x['share'] for x in redistribute(items)]==[25,38,37]
    items[2]['removed']=True
    assert [x['share'] for x in redistribute(items)]==[25,75,0]
    items[2]['removed']=False
    assert sum(x['share'] for x in redistribute(items))==100

@pytest.mark.parametrize('items',[[row(1,70,True),row(2,40,True)],[row(1,25,True)],[row(1,removed=True)],[row(1),row(1)]])
def test_impossible_distribution_rejected(items):
    with pytest.raises(ValueError):redistribute(items)

def test_hundred_percent_pin_allows_zero_free():
    assert [x['share'] for x in redistribute([row(1,100,True),row(2)])]==[100,0]

def test_remote_removal_retained_and_pin_preserved():
    out=merge_remote([{'offer_id':1,'share':100}],[row(1,25,True),row(2,75)])
    assert out==[row(1,100,True),row(2,0,False,True)]

def test_one_to_one_hundred():
    for n in range(1,101):
        out=redistribute([row(i) for i in range(n)])
        assert sum(x['share'] for x in out)==100
        assert max(x['share'] for x in out)-min(x['share'] for x in out)<=1
