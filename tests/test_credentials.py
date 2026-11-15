from contextlib import redirect_stdout
import ctypes
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from cryptoir import accounts as a, credentials as c

KEY=a.Credentials('tabdeal_hmac','SYNTHETIC-KEY','SYNTHETIC-SECRET')


class CredentialPayloadTests(unittest.TestCase):
    def test_roundtrip_uses_only_namespaced_native_store_and_status_never_discloses_key(self):
        saved={}
        def write(target,blob):saved[target]=blob
        with patch.object(c,'supported',return_value=True),patch.object(c,'_write_blob',side_effect=write),\
                patch.object(c,'_read_blob',side_effect=lambda target:saved.get(target)):
            c.save_credentials('tabdeal',KEY)
            loaded=c.load_credentials('tabdeal')
            self.assertEqual((loaded.scheme,loaded.key,loaded.secret),(KEY.scheme,KEY.key,KEY.secret))
            status=c.credential_status('tabdeal')
            self.assertTrue(status['stored'])
            self.assertNotIn('SYNTHETIC',json.dumps(status))
            self.assertEqual(repr(loaded),'<Credentials redacted>')
        self.assertEqual(list(saved),[c.target_name('tabdeal')])

    def test_namespace_is_venue_and_checkout_scoped_without_exposing_path(self):
        one=c.target_name('tabdeal')
        self.assertNotEqual(one,c.target_name('nobitex'))
        with patch.object(c,'__file__',str(Path.cwd()/'synthetic-checkout'/'cryptoir'/'credentials.py')):
            two=c.target_name('tabdeal')
        self.assertNotEqual(one,two)
        self.assertRegex(one,r'^cryptoIR/[a-f0-9]{24}/tabdeal$')
        for venue in ('../wallex','other','tabdeal/extra'):
            with self.assertRaises(c.CredentialStoreError):c.target_name(venue)

    def test_unsupported_platform_has_no_plaintext_fallback(self):
        with patch.object(c,'supported',return_value=False),patch.object(c,'_read_blob') as read:
            self.assertIsNone(c.load_credentials('tabdeal'))
            read.assert_not_called()
            with self.assertRaisesRegex(c.CredentialStoreError,'windows_credential_manager_required'):
                c.save_credentials('tabdeal',KEY)

    def test_wrong_venue_corrupt_duplicate_and_oversize_payloads_fail_closed(self):
        valid={'schema':1,'venue':'tabdeal','scheme':KEY.scheme,'key':KEY.key,'secret':KEY.secret}
        cases=[b'bad',b'{"schema":1,"schema":1}',b'x'*(c.MAX_BLOB+1),
               json.dumps({**valid,'venue':'wallex'}).encode(),
               json.dumps({**valid,'scheme':'wallex_key','secret':None}).encode(),
               json.dumps({**valid,'extra':'SYNTHETIC-SECRET'}).encode()]
        for body in cases:
            with self.subTest(length=len(body)),patch.object(c,'supported',return_value=True),patch.object(c,'_read_blob',return_value=body):
                with self.assertRaises(c.CredentialStoreError) as caught:c.load_credentials('tabdeal')
                self.assertNotIn('SYNTHETIC',str(caught.exception))
        with patch.object(c,'_write_blob') as write:
            with self.assertRaises(c.CredentialStoreError):c.save_credentials('wallex',KEY)
            write.assert_not_called()

    def test_delete_uses_only_requested_scoped_target(self):
        with patch.object(c,'_delete_blob',return_value=True) as delete:
            result=c.forget_credentials('wallex')
        delete.assert_called_once_with(c.target_name('wallex'))
        self.assertTrue(result['removed'])
        self.assertFalse(result['exchange_key_revoked'])


class CredentialNativeTests(unittest.TestCase):
    def test_write_sets_user_local_persistence_and_wipes_temporary_buffer(self):
        class API:
            def CredWriteW(self,pointer,flags):
                native=ctypes.cast(pointer,ctypes.POINTER(c.NativeCredential)).contents
                self.pointer=native.CredentialBlob
                self.buffer_owner=pointer._obj
                self.size=native.CredentialBlobSize
                self.contents=ctypes.string_at(self.pointer,self.size)
                self.persist=native.Persist
                self.type=native.Type
                self.target=native.TargetName
                return 1
        api=API()
        with patch.object(c,'_api',return_value=api):c._write_blob('synthetic-target',b'SYNTHETIC')
        self.assertEqual(api.contents,b'SYNTHETIC')
        self.assertEqual(api.persist,c.LOCAL_MACHINE)
        self.assertEqual(api.type,c.GENERIC)
        self.assertEqual(api.target,'synthetic-target')
        self.assertEqual(ctypes.string_at(api.pointer,api.size),b'\0'*api.size)

    def test_read_frees_and_wipes_native_copy_even_when_metadata_invalid(self):
        class API:
            def __init__(self,valid):
                self.buffer=(ctypes.c_ubyte*9).from_buffer_copy(b'SYNTHETIC')
                self.native=c.NativeCredential(Type=c.GENERIC,TargetName='synthetic-target',
                    CredentialBlobSize=9,CredentialBlob=self.buffer,Persist=c.LOCAL_MACHINE if valid else 1)
                self.freed=False
            def CredReadW(self,target,kind,flags,out):
                ctypes.cast(out,ctypes.POINTER(ctypes.POINTER(c.NativeCredential)))[0]=ctypes.pointer(self.native)
                return 1
            def CredFree(self,pointer):self.freed=True
        for valid in (True,False):
            api=API(valid)
            with self.subTest(valid=valid),patch.object(c,'_api',return_value=api):
                if valid:self.assertEqual(c._read_blob('synthetic-target'),b'SYNTHETIC')
                else:
                    with self.assertRaises(c.CredentialStoreError):c._read_blob('synthetic-target')
            self.assertTrue(api.freed)
            self.assertEqual(bytes(api.buffer),b'\0'*9)


class CredentialCommandTests(unittest.TestCase):
    def report(self,status='accepted'):
        return {'results':[{'operation':'account','status':status}],
                'all_requested_reads_accepted':status=='accepted'}

    def run_check(self,*,save=False,stored=None,status='accepted'):
        out=io.StringIO()
        with patch.object(c,'supported',return_value=True),patch.object(c,'load_credentials',return_value=stored) as load,\
                patch.object(c,'save_credentials') as write,patch.object(a,'prompt_credentials',return_value=KEY) as prompt,\
                patch.object(a,'inspect_account',return_value=self.report(status)) as inspect,\
                patch.object(a,'save_assessment',return_value=Path('tabdeal-synthetic.json')),redirect_stdout(out):
            result=a.account_check('tabdeal',save=save)
        return result,json.loads(out.getvalue()),load,write,prompt,inspect

    def test_saved_credentials_are_reused_without_prompt_or_new_write(self):
        code,out,load,write,prompt,inspect=self.run_check(stored=KEY)
        self.assertEqual(code,0)
        prompt.assert_not_called();write.assert_not_called()
        inspect.assert_called_once_with('tabdeal',KEY)
        self.assertEqual(out['credential_source'],'windows_credential_manager')
        self.assertTrue(out['credentials_stored'])
        self.assertNotIn('SYNTHETIC',json.dumps(out))

    def test_explicit_save_prompts_and_persists_even_when_account_remains_partial(self):
        for status in ('partial','unavailable'):
            with self.subTest(status=status):
                code,out,load,write,prompt,_=self.run_check(save=True,status=status)
                self.assertEqual(code,1)
                load.assert_not_called()
                prompt.assert_called_once_with('tabdeal',None,saving=True)
                write.assert_called_once_with('tabdeal',KEY)
                self.assertTrue(out['credentials_stored'])
                self.assertNotIn('SYNTHETIC',json.dumps(out))

    def test_absent_saved_key_uses_hidden_prompt_without_saving(self):
        code,out,_,write,prompt,_=self.run_check()
        self.assertEqual(code,0)
        prompt.assert_called_once_with('tabdeal',None,saving=False)
        write.assert_not_called()
        self.assertFalse(out['credentials_stored'])

    def test_vault_failure_never_falls_back_to_another_store_or_prompt(self):
        out=io.StringIO()
        with patch.object(c,'load_credentials',side_effect=c.CredentialStoreError('credential_vault_read_failed')),\
                patch.object(a,'prompt_credentials') as prompt,patch.object(a,'inspect_account') as inspect,redirect_stdout(out):
            self.assertEqual(a.account_check('tabdeal'),1)
        prompt.assert_not_called();inspect.assert_not_called()
        self.assertEqual(json.loads(out.getvalue())['reason'],'credential_vault_read_failed')

    def test_failed_write_blocks_network_and_post_save_failure_preserves_storage_status(self):
        for fail_write in (True,False):
            out=io.StringIO()
            with self.subTest(fail_write=fail_write),patch.object(c,'supported',return_value=True),\
                    patch.object(a,'prompt_credentials',return_value=KEY),\
                    patch.object(c,'save_credentials',side_effect=c.CredentialStoreError('credential_vault_write_failed') if fail_write else None),\
                    patch.object(a,'inspect_account',side_effect=RuntimeError('SYNTHETIC-SECRET')) as inspect,redirect_stdout(out):
                self.assertEqual(a.account_check('tabdeal',save=True),1)
            if fail_write:inspect.assert_not_called()
            self.assertEqual(json.loads(out.getvalue())['credentials_stored'],not fail_write)
            self.assertNotIn('SYNTHETIC',out.getvalue())

    def test_saved_nobitex_scheme_is_reused_but_conflicting_legacy_flag_is_rejected(self):
        token=a.Credentials('nobitex_token','SYNTHETIC-TOKEN')
        newer=a.Credentials('nobitex_ed25519','SYNTHETIC-PUBLIC','SYNTHETIC-PRIVATE')
        for credential,legacy,accepted in ((token,False,True),(newer,True,False)):
            with self.subTest(legacy=legacy),patch.object(c,'load_credentials',return_value=credential),\
                    patch.object(a,'prompt_credentials') as prompt,patch.object(a,'inspect_account',return_value=self.report()) as inspect,\
                    patch.object(a,'save_assessment',return_value=Path('nobitex-synthetic.json')),redirect_stdout(io.StringIO()):
                self.assertEqual(a.account_check('nobitex',legacy_token=legacy),0 if accepted else 1)
            prompt.assert_not_called()
            if accepted:inspect.assert_called_once_with('nobitex',credential)
            else:inspect.assert_not_called()

    def test_cli_exposes_save_status_and_forget_without_secret_arguments(self):
        from cryptoir.__main__ import main
        with patch('sys.argv',['cryptoir','account-check','--venue','tabdeal','--save-credentials']),\
                patch.object(a,'account_check',return_value=0) as command:
            self.assertEqual(main(),0)
            command.assert_called_once_with('tabdeal',legacy_token=False,save=True)
        for name,function in (('credentials-status','credential_status'),('credentials-forget','forget_credentials')):
            with patch('sys.argv',['cryptoir',name,'--venue','tabdeal']),patch.object(c,function,return_value={}) as command,redirect_stdout(io.StringIO()):
                self.assertEqual(main(),0)
                command.assert_called_once_with('tabdeal')


if __name__=='__main__':
    unittest.main()
