from ppt_rubric.common import file_digest
from ppt_rubric.contract_regression import visual_probe


def test_expected_labels_and_candidate_identity_are_withheld(tmp_path):
    # The fake image bytes only exercise the API boundary; no visual claim here.
    image = tmp_path / 'image.png'
    image.write_bytes(b'fixture')
    sample = {'id': 'local-01', 'ground_truth': 'overlap', 'case_id': 'secret-case',
              'candidate_id': 'secret-model', 'observable_text_relationships': ['SECRET EXPECTED RATIONALE'],
              'image_path': str(image), 'image': {'sha256': file_digest(image)},
              'original_page': {'path': str(image), 'sha256': file_digest(image)}}
    class Client:
        def ask(self, role, system, payload, images, validate):
            assert 'secret' not in repr(payload).lower()
            assert 'SECRET EXPECTED RATIONALE' not in system + repr(payload)
            assert 'ground_truth' not in repr(payload)
            assert 'overlap' not in payload['document_id']
            return {'pages': [{'page': 1, 'verdicts': [
                {'rubric_id': item['id'], 'answer': 'yes', 'pages': [1], 'evidence': 'fixture', 'defect': None}
                for item in payload['criteria']]}]}
    visual_probe(sample, Client())
