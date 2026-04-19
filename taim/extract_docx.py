from docx import Document
import os

def extract_docx_text(docx_path):
    doc = Document(docx_path)
    text = []
    for para in doc.paragraphs:
        text.append(para.text)
    return '\n'.join(text)

if __name__ == '__main__':
    base = os.path.dirname(__file__)
    files = [
        os.path.join(base, '../hackathon-tumai/README.docx'),
        os.path.join(base, '../hackathon-tumai/TUM.ai x Spherecast.docx'),
    ]
    for f in files:
        print(f'--- {os.path.basename(f)} ---')
        print(extract_docx_text(f))
        print('\n' + '='*40 + '\n')
