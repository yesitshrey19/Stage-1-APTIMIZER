import pytest
import aptspeed

def test_classify():
    assert aptspeed.classify("hi") == aptspeed.NONE
    assert aptspeed.classify("hello") == aptspeed.NONE
    assert aptspeed.classify("thanks") == aptspeed.NONE
    assert aptspeed.classify("what can you do?") == aptspeed.NONE
    
    assert aptspeed.classify("tell me about the project") == aptspeed.LIGHT
    assert aptspeed.classify("what is the total area") == aptspeed.LIGHT
    
    assert aptspeed.classify("what does clause 4.2 say") == aptspeed.FULL
    assert aptspeed.classify("show me the structural module") == aptspeed.FULL
    assert aptspeed.classify("is the building 50m tall?") == aptspeed.FULL

def test_is_capability_question():
    assert aptspeed.is_capability_question("what can you do") == True
    assert aptspeed.is_capability_question("who are you") == True
    assert aptspeed.is_capability_question("hi") == False
