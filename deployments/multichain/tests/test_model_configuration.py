"""Dedicated configuration aliases only; fixture values are not credentials."""
import os
import pytest
from autodefi.config import MODEL_VARIABLES,model_env
from autodefi.trading import ProtocolModel
from tools.activate_preview import load_environment


@pytest.fixture
def clean_model_environment(monkeypatch):
    for prefix in ('VELA_','VERA_PROTOCOL_','ADP_'):
        for name in MODEL_VARIABLES:monkeypatch.delenv(prefix+name,raising=False)


def test_vela_model_aliases_load_without_inheriting_other_app_settings(clean_model_environment,monkeypatch):
    monkeypatch.setenv('VELA_MODEL','fixture-model')
    monkeypatch.setenv('VELA_MODEL_KEY','fixture-key-not-a-credential')
    monkeypatch.setenv('VELA_MODEL_ENDPOINT','https://model.example.invalid/v1/responses')
    monkeypatch.setenv('WHY_DISCOVERY_MODEL_KEY','must-not-use')
    model=ProtocolModel()
    assert model.ready and model.model=='fixture-model'
    assert model.key=='fixture-key-not-a-credential'
    assert model.endpoint=='https://model.example.invalid/v1/responses'


@pytest.mark.parametrize('name',MODEL_VARIABLES)
def test_conflicting_aliases_fail_without_exposing_values(clean_model_environment,monkeypatch,name):
    monkeypatch.setenv('VELA_'+name,'fixture-first-value')
    monkeypatch.setenv('VERA_PROTOCOL_'+name,'fixture-second-value')
    with pytest.raises(ValueError) as error:model_env(name)
    assert 'fixture' not in str(error.value)
    monkeypatch.setenv('VERA_PROTOCOL_'+name,'fixture-first-value')
    assert model_env(name)=='fixture-first-value'


def test_missing_model_credentials_stay_disconnected(clean_model_environment,monkeypatch):
    assert not ProtocolModel().ready
    monkeypatch.setenv('VELA_MODEL','fixture-model')
    assert not ProtocolModel().ready


def test_private_model_file_is_loaded_without_shell_evaluation(clean_model_environment,monkeypatch,tmp_path):
    for name in MODEL_VARIABLES:monkeypatch.setenv('VELA_'+name,'');monkeypatch.delenv('VELA_'+name)
    path=tmp_path/'preview.env';path.write_text('VELA_MODEL=fixture-model\nVELA_MODEL_KEY="$(fixture-never-execute)"\n')
    path.chmod(0o600);load_environment(path)
    assert ProtocolModel().ready and os.environ['VELA_MODEL_KEY']=='$(fixture-never-execute)'
    path.write_text('VERA_PROTOCOL_MODEL_KEY=different-fixture-value\n')
    with pytest.raises(ValueError) as error:load_environment(path)
    assert 'different-fixture-value' not in str(error.value)
    assert 'VERA_PROTOCOL_MODEL_KEY' not in os.environ
