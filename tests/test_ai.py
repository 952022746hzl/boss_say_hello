import json
import httpx
import pytest


@pytest.mark.parametrize('value', [101,-1,True,70.5,'70'])
def test_invalid_score_rejected(value):
    from boss_say_hello.ai.schemas import parse_assessment, AiFailure
    with pytest.raises(AiFailure):
        parse_assessment({'score':value,'reason':'r','evidence':[],'gaps':[],'missing':[]})


async def test_connection_request_has_no_resume(config):
    from boss_say_hello.ai.client import AiService
    bodies=[]
    def respond(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200,json={'id':'c1','object':'chat.completion','created':1,'model':'test-model','choices':[{'index':0,'message':{'role':'assistant','content':'OK'},'finish_reason':'stop'}]})
    ai=AiService(config(), 'secret-for-test', httpx.MockTransport(respond))
    await ai.test_connection(); await ai.close()
    assert len(bodies)==1
    assert bodies[0]['messages']==[{'role':'user','content':'Reply OK.'}]


@pytest.mark.parametrize('mode',['timeout','bad_json','missing','empty','429'])
async def test_model_failures_never_return_score(mode, config, snapshot, job, caplog):
    from boss_say_hello.ai.client import AiService
    from boss_say_hello.ai.schemas import AiFailure
    def respond(request):
        if mode=='timeout': raise httpx.ReadTimeout('secret-for-test',request=request)
        if mode=='429': return httpx.Response(429,json={'error':{'message':'secret-for-test'}})
        content={'bad_json':'not json','missing':'{"score":99}','empty':''}[mode]
        return httpx.Response(200,json={'id':'c1','object':'chat.completion','created':1,'model':'test','choices':[{'index':0,'message':{'role':'assistant','content':content},'finish_reason':'stop'}]})
    ai=AiService(config(), 'secret-for-test',httpx.MockTransport(respond))
    with pytest.raises(AiFailure) as caught: await ai.assess(snapshot(),job())
    assert 'secret-for-test' not in str(caught.value)+caplog.text
    await ai.close()
