from common import RuntimeException


def testRuntimeException():
    try:
        try:
            raise RuntimeException("Test error", "start")
        except RuntimeException as e:
            e.appendExecPath("end")
            raise e
    except RuntimeException as e:
        assert str(e) == "start->end: Test error"
    except Exception as e:
        assert False, f"Unexpected exception type: {type(e)}"
