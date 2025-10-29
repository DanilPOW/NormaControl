import fitz
import sys
from scripts.image_checker import check_images

def main():
    pdf_path = 'tests/test_image_iscenter.pdf'
    doc = fitz.open(pdf_path)
    result = check_images(doc, pdf_path)

    print('User Summary:', result['user_summary'])
    print('Admin Details:', result['admin_details'])

    if '✅' in result['user_summary']:
        print('Image validation passed')
        return 0
    else:
        print('Image validation failed')
        return 1

if __name__ == "__main__":
    sys.exit(main())
