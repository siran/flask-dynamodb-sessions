import pytest
from mock import call
import re
from pytest_mock import mocker
import flask
import flask.sessions
import boto3
from flask_dynamodb_sessions import (
    Session,
    DynamodbSessionInterface,
    DynamodbSession
)


def create_test_app(**kwargs):
    app = flask.Flask(__name__)
    app.config.update(**kwargs)
    Session(app)

    @app.route('/test_route')
    def test_route():
        flask.session['x'] = 'foo'
        return flask.make_response('', 200)

    return app


def test_save_uses_header(mocker):
    boto_mock = mocker.patch('flask_dynamodb_sessions.boto3.client')
    boto_mock_instance = boto_mock()

    app = create_test_app(
        SESSION_DYNAMODB_USE_HEADER=True
    )
    mocker.spy(boto_mock, 'update_item')

    response = app.test_client().get('/test_route')

    # Find the session ID that was passed to update_item()
    session_id = None
    match = re.search("Key={'id': {'S': '(.+?)'}}", str(boto_mock_instance.update_item.call_args))
    if match:
        session_id = match.group(1)

    assert 'X-SessionId' in response.headers
    assert response.headers['X-SessionId'] == session_id
    assert 'Set-Cookie' not in response.headers


def test_read_uses_header(mocker):
    expected_session_id = 'foobar'
    boto_mock = mocker.patch('flask_dynamodb_sessions.boto3.client')
    boto_mock_instance = boto_mock()
    boto_mock_instance.get_item.return_value = {'Item': {'data': ''}}

    app = create_test_app(
        SESSION_DYNAMODB_USE_HEADER=True
    )
    mocker.spy(boto_mock, 'get_item')

    response = app.test_client().get('/test_route', headers={'X-SessionId': expected_session_id})

    # Find the session ID that was passed to get_item()
    actual_session_id = None
    match = re.search("Key={'id': {'S': '(.+?)'}}", str(boto_mock_instance.get_item.call_args))
    if match:
        actual_session_id = match.group(1)

    assert actual_session_id == expected_session_id


def test_consistent_read_default_false(mocker):
    boto_mock = mocker.patch('flask_dynamodb_sessions.boto3.client')
    boto_mock_instance = boto_mock()
    boto_mock_instance.get_item.return_value = {'Item': {'data': ''}}

    app = create_test_app(
        SESSION_DYNAMODB_USE_HEADER=True
    )
    mocker.spy(boto_mock, 'get_item')

    response = app.test_client().get('/test_route', headers={'X-SessionId': 'foo'})

    # Validate ConsistentRead setting
    assert 'ConsistentRead=False' in str(boto_mock_instance.get_item.call_args)


def test_consistent_read_true(mocker):
    boto_mock = mocker.patch('flask_dynamodb_sessions.boto3.client')
    boto_mock_instance = boto_mock()
    boto_mock_instance.get_item.return_value = {'Item': {'data': ''}}

    app = create_test_app(
        SESSION_DYNAMODB_USE_HEADER=True,
        SESSION_DYNAMODB_CONSISTENT_READ=True
    )
    mocker.spy(boto_mock, 'get_item')

    response = app.test_client().get('/test_route', headers={'X-SessionId': 'foo'})

    # Validate ConsistentRead setting
    assert 'ConsistentRead=True' in str(boto_mock_instance.get_item.call_args)


class TestDynamoSessionInterface:

    @pytest.fixture
    def base(self):
        """ instantiate a dynamo sesson interface
            instance for tests

            overwrite all keyword defaults
        """
        dsi = DynamodbSessionInterface(
            table='test-table',
            permanent=False,
            endpoint='http://test-ep',
            region='us-compton-2',
            ttl='5963',
            use_header=True,
            header_name='test-header',
            consistent_read=True
        )

        return dsi

    def test_boto_client(self, base, mocker):
        """ Test boto3.client created with
            instantiated config
        """
        boto_mock = mocker.patch('flask_dynamodb_sessions.boto3')

        boto_client = base.boto_client()

        assert boto_mock.method_calls ==\
            [call.client('dynamodb',
                         endpoint_url='http://test-ep',
                         region_name='us-compton-2')]

    def test_open_session_no_id(self, base, mocker):
        """ Test open_session with id==None condition

            Note:
                We should get a DynamoSession back
                with a new session id
        """
        uuid_mock = mocker.patch('flask_dynamodb_sessions.uuid4')
        uuid_mock.return_value = 'mock-uuid'
        app_mock = mocker.MagicMock(flask.Flask(__name__))
        req_mock = mocker.MagicMock()
        req_mock.headers.get.return_value = None

        result = base.open_session(app_mock, req_mock)

        assert result.sid == 'mock-uuid'

    def test_open_session_header(self, base, mocker):
        """ Test open_session in use_header mode
            and id present

            Note:
                We should get a DynamoSession
                with the mocked id and request.headers
                called with configured header_name
        """
        req_mock = mocker.MagicMock()
        req_mock.headers.get.return_value = 'header-id'
        base.dynamo_get = mocker.MagicMock(return_value=None)

        app_mock = mocker.MagicMock(flask.Flask(__name__))

        result = base.open_session(app_mock, req_mock)

        # assert session id
        assert result.sid == 'header-id'
        # assert request headers called
        # w/config'd header_name
        assert req_mock.method_calls ==\
            [call.headers.get('test-header')]

    def test_open_session_cookie(self, base, mocker):
        """ Test open_session in cookie mode w/id
            present

            Note:
                We should get a DynamoSession
                with the mocked id and request.cookies
                being called using flask apps session
                cookie name prop

        """
        # set use_header to false
        base.use_header = False
        req_mock = mocker.MagicMock()
        req_mock.cookies.get.return_value='cookie-id'
        base.dynamo_get = mocker.MagicMock(return_value=None)

        app_mock = mocker.MagicMock(flask.Flask(__name__))

        result = base.open_session(app_mock, req_mock)

        # assert cookie was called with
        # flask app's session_cookie_name prop
        assert req_mock.method_calls ==\
            [call.cookies.get(app_mock.session_cookie_name)]

        # assert returned session id
        assert result.sid == 'cookie-id'


    def test_open_session_hydrate(self, base, mocker):
        """ Test open_session as if we were resuming
            and already established session saved
            in dynamo
        """
        req_mock = mocker.MagicMock()
        req_mock.headers.get.return_value = 'header-id'
        base.dynamo_get = mocker.MagicMock(return_value='session-data')
        base.hydrate_session = mocker.MagicMock(return_value={'hydrated': 'data'})

        app_mock = mocker.MagicMock(flask.Flask(__name__))

        result = base.open_session(app_mock, req_mock)

        # assert hydrate was called with mocked session data
        assert base.hydrate_session.call_args_list ==\
            [call('session-data')]

        # assert returned session data
        assert dict(result) == {'hydrated': 'data'}

    @pytest.fixture
    def dynamo_get_return(self):
        """
        """
        return {
            'Item': {
                'data': {
                    'S': 'session-body'
                }
            }
        }

    def test_dynamo_get(self,
                        base,
                        dynamo_get_return,
                        mocker):
        """ Test dynamo_get with mock return
        """
        boto_mock = mocker.MagicMock(boto3.client('dynamodb'))
        boto_mock.get_item.return_value = dynamo_get_return
        base._boto_client = boto_mock

        res = base.dynamo_get('mock-id')

        # assert return value
        assert res == 'session-body'

        # assert boto method calls
        assert boto_mock.method_calls ==\
            [call.get_item(
                ConsistentRead=True,
                Key={'id': {'S': 'mock-id'}},
                TableName='test-table')]

    def test_dynamo_get_exception(self, base, mocker):
        """ Test dynamo_get with api raising exception
        """
        boto_mock = mocker.MagicMock(boto3.client('dynamodb'))
        boto_mock.get_item.side_effect = (Exception('get_item error'))
        base._boto_client = boto_mock
        print_mock = mocker.patch('flask_dynamodb_sessions.print')

        res = base.dynamo_get('mock')

        # result should be none
        assert res is None

        # exception should have printed
        assert print_mock.mock_calls ==\
            [call('DYNAMO SESSION GET ITEM ERR: ',
                  'get_item error')]

    def test_dynamo_save(self, base, mocker):
        """
        """
        pass

    def test_dynamo_save_exception(self, base, mocker):
        """
        """
        pass

    def test_delete_session(self, base, mocker):
        """
        """
        pass

    def test_delete_session_exception(self, base, mocker):
        """
        """
        pass

    def test_pickle_session(self, base, mocker):
        """
        """
        pass

    def test_hydrate_session(self, base, mocker):
        """
        """
        pass



