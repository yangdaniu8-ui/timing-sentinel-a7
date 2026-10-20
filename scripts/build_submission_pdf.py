"""Create the competition PDF directly from the template-structured Markdown.

Uses bundled ReportLab/Poppler. Does not author or convert a DOCX or edit photos.
"""
from pathlib import Path
import argparse
import html
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, PageTemplate, Frame, Paragraph,
    Spacer, Table, TableStyle, KeepTogether, Flowable, HRFlowable)
from reportlab.lib.utils import ImageReader

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'report/technical_report_template_20261017.md'
REFERENCE=ROOT/'references/submission_20261017/report_template.docx'
PHOTO_ROOT=ROOT/'references/submission_20261020/photos'
NS={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}

class Photo(Flowable):
    def __init__(self,path,width,height,rotate=False):
        super().__init__();self.path=path;self.width=width;self.height=height;self.rotate=rotate
    def draw(self):
        self.canv.saveState()
        if self.rotate:self.canv.translate(self.width,self.height);self.canv.rotate(180)
        self.canv.drawImage(ImageReader(str(self.path)),0,0,width=self.width,height=self.height,mask='auto')
        self.canv.restoreState()

def rich(text):
    text=html.escape(text)
    text=re.sub(r'`([^`]+)`',r'<font name="Song">\1</font>',text)
    text=re.sub(r'\*\*([^*]+)\*\*',r'<b>\1</b>',text)
    # Keep external URLs ordinary text; no account or task-host paths.
    return text

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args();args.out.parent.mkdir(parents=True,exist_ok=True)
    assert not args.out.exists(),'Do not overwrite a reviewed artifact'
    text=SOURCE.read_text(encoding='utf-8')
    assert not re.search(r'互校|独立仪器|未校准|尚未.*校准|PHOTO_REQUIRED',text)
    pdfmetrics.registerFont(TTFont('Song','C:/Windows/Fonts/simsun.ttc',subfontIndex=0))
    pdfmetrics.registerFont(TTFont('Hei','C:/Windows/Fonts/simhei.ttf'))
    pdfmetrics.registerFontFamily('Song',normal='Song',bold='Hei',italic='Song',boldItalic='Hei')
    with zipfile.ZipFile(REFERENCE) as z:reference=ET.fromstring(z.read('word/document.xml'))
    sect=reference.find('.//w:sectPr',NS)
    def val(element,key):return float(sect.find('w:'+element,NS).get('{'+NS['w']+'}'+key))/20
    page=(val('pgSz','w'),val('pgSz','h'))
    left,right,top,bottom=(val('pgMar',x) for x in ('left','right','top','bottom'))
    width=page[0]-left-right
    styles={
      'Title':ParagraphStyle('Title',fontName='Hei',fontSize=18,leading=28,alignment=TA_CENTER,spaceAfter=16),
      'H2':ParagraphStyle('H2',fontName='Hei',fontSize=14,leading=22,spaceBefore=14,spaceAfter=9,keepWithNext=True),
      'H3':ParagraphStyle('H3',fontName='Hei',fontSize=12,leading=20,spaceBefore=11,spaceAfter=6,keepWithNext=True),
      'H4':ParagraphStyle('H4',fontName='Hei',fontSize=12,leading=20,spaceBefore=10,spaceAfter=5,keepWithNext=True),
      'Body':ParagraphStyle('Body',fontName='Song',fontSize=12,leading=20.8,spaceAfter=8,firstLineIndent=24,wordWrap='CJK'),
      'List':ParagraphStyle('List',fontName='Song',fontSize=12,leading=20.8,spaceAfter=6,wordWrap='CJK'),
      'Cell':ParagraphStyle('Cell',fontName='Song',fontSize=9.5,leading=14.5,wordWrap='CJK'),
      'Header':ParagraphStyle('Header',fontName='Hei',fontSize=9.5,leading=14.5,wordWrap='CJK'),
      'Caption':ParagraphStyle('Caption',fontName='Song',fontSize=10.5,leading=16,alignment=TA_CENTER,spaceBefore=5,spaceAfter=12),
      'Ref':ParagraphStyle('Ref',fontName='Song',fontSize=10.5,leading=17,spaceAfter=7,wordWrap='CJK'),
    }
    story=[];lines=text.splitlines();i=0;figures=[]
    while i<len(lines):
        line=lines[i].strip()
        if not line or line.startswith('<!--'):i+=1;continue
        heading=re.match(r'^(#{1,4}) (.+)',line)
        if heading:
            level=len(heading[1]);name='Title' if level==1 else 'H'+str(level)
            story.append(Paragraph(rich(heading[2]),styles[name]));i+=1;continue
        picture=re.match(r'!\[([^\]]*)\]\(([^)]+)\)',line)
        if picture:
            path=(SOURCE.parent/picture[2]).resolve();assert path.is_file(),path
            with PILImage.open(path) as im:iw,ih=im.size
            # Layout rotation only. The preserved JPEG bytes remain unchanged.
            rotate=path.name in ('stm32_wiring.jpg','fpga_back.jpg')
            maxheight=300 if path.suffix.lower()=='.jpg' else 260
            scale=min(width/iw,maxheight/ih)
            figure=Photo(path,iw*scale,ih*scale,rotate=rotate)
            figure.hAlign='CENTER'
            j=i+1
            while j<len(lines) and not lines[j].strip():j+=1
            caption=lines[j].strip() if j<len(lines) and re.match(r'^图\d+ ',lines[j].strip()) else None
            items=[Spacer(1,4),figure]
            if caption:items.append(Paragraph(rich(caption),styles['Caption']));i=j+1
            else:i+=1
            story.append(KeepTogether(items));figures.append(dict(file=picture[2],caption=caption,layout_rotated_180=rotate))
            continue
        if line.startswith('|'):
            raw=[]
            while i<len(lines) and lines[i].strip().startswith('|'):
                cells=[x.strip() for x in lines[i].strip().strip('|').split('|')]
                if not all(re.fullmatch(r':?-+:?',x or '-') for x in cells):raw.append(cells)
                i+=1
            columns=len(raw[0]);assert all(len(r)==columns for r in raw)
            if columns==2:ratios=[.35,.65]
            elif columns==3:ratios=[.27,.39,.34]
            elif columns==4:ratios=[.18,.19,.15,.48]
            elif columns==5:ratios=[.12,.29,.12,.28,.19]
            else:ratios=[.13,.16,.08,.19,.19,.25]
            assert len(ratios)==columns
            matrix=[[Paragraph(rich(c),styles['Header' if k==0 else 'Cell']) for c in row] for k,row in enumerate(raw)]
            table=Table(matrix,colWidths=[width*x for x in ratios],repeatRows=1,hAlign='CENTER')
            table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#E9EDF2')),
                ('GRID',(0,0),(-1,-1),.4,colors.HexColor('#C9CED4')),('VALIGN',(0,0),(-1,-1),'MIDDLE'),
                ('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),
                ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]))
            story.extend([Spacer(1,4),table,Spacer(1,10)]);continue
        paragraph=[line];i+=1
        while i<len(lines) and lines[i].strip() and not re.match(r'^(#|!\[|\||<!--|\d+\.)',lines[i].strip()):
            paragraph.append(lines[i].strip());i+=1
        content=''.join(paragraph)
        role='Ref' if content.startswith('[') else ('List' if re.match(r'^\d+\.',content) else 'Body')
        story.append(Paragraph(rich(content),styles[role]))
    def footer(canvas,doc):
        canvas.saveState();canvas.setFont('Song',9);canvas.setFillColor(colors.HexColor('#555555'))
        canvas.drawCentredString(page[0]/2,32,str(doc.page));canvas.restoreState()
    doc=BaseDocTemplate(str(args.out),pagesize=page,leftMargin=left,rightMargin=right,topMargin=top,bottomMargin=bottom,
                        title='FPGA跨协议时序诊断平台',author='',subject='技术文档',creator='Timing Sentinel A7')
    doc.addPageTemplates(PageTemplate(id='main',frames=[Frame(left,bottom,width,page[1]-top-bottom,
        leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)],onPage=footer))
    doc.build(story)
    (args.out.parent/(args.out.stem+'_build.json')).write_text(json.dumps(dict(passed=True,source=str(SOURCE),
        output=str(args.out),figures=figures,template_page_size_points=page,template_margins_points=[left,right,top,bottom],
        photo_pixels_edited=False,docx_authored=False,visual_qa_pending=True),ensure_ascii=False,indent=2),encoding='utf-8')
    print('TECHNICAL_PDF_CREATED '+str(args.out))

if __name__=='__main__':main()
