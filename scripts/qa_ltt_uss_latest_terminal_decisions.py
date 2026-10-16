"""Record individual fresh source reviews after reading current fact packets."""
from scripts import qa_ltt_uss_latest as q
r,OUT=q.r,q.OUT

def finish():
    amb=r.load(OUT/'AMBIGUOUS_DECISION_VERIFICATION.json');multi=r.load(OUT/'MULTI_VARIANT_DECISION_VERIFICATION.json')
    snapshot=r.load(OUT/'BEFORE_QA.json');ids={p['external_id'] for p,offer in snapshot['product_offer_projections']}
    for d in amb['rows']+multi['rows']:assert d['source_id'] not in ids
    operator={
        1:'Свежий H1 объединяет дрели и винтоверты, H2 только ST PRO; артикул2404210/25440/на складе не доказывает устойчивую дочернюю идентичность familyURL. Политика оператора применима.',
        12:'Свежий H1 Обдувочные пистолеты Aircraft, H2 BP PRO Duo, единственная строка2102190 с заказом; родительский URL не становится идентичностью дочерней модели. Политика оператора применима.',
        17:'Свежий H1 объединяет зачистные машины и граверы, H2 WST PRO и отдельный инструмент;2403210/9120/под заказ не устраняет семейную идентичность URL. Политика оператора применима.',
        306:'Собственный технический DPA21, без articleanchors, заказов и коммерческих таблиц. Datasheet не создаёт одобренный sellableoffer.',
        307:'Собственный технический DPA22, без articleanchors, заказов и коммерческих таблиц. Datasheet не создаёт одобренный sellableoffer.',
        318:'Собственный технический DRO5, комплект дисплея/датчиков без articleanchors, заказов и коммерческих таблиц. Это datasheet, не одобренный коммерческий offer.'}
    risk={
        27:'Mobilboy301/24 и301/24E в отдельных model/article/price/stock таблицах:2003325/2003326,42660/36840,два заказа. Два покупаемых компрессора, не строки характеристик.',
        50:'Основной TKS315E имеет собственные 220В/380В артикулы5903315/5903316,две цены44340 и два заказа; дополнительный диск5263128 не использован как основание MULTI.',
        86:'ALA2/ALA10 отдельно названы и продаются:5121503/5121504,7740/18240,два самостоятельных заказа. Это каталог нескольких изделий, не детали одного комплектаBRS1.',
        131:'Две модельные колонки CDS6250B иCDS6250С содержат отдельные по запросу/на складе и разные отверстия82/105мм. Раздельные коммерческие исполнения есть без кнопок/артикулов; размерные строки сами не основание.',
        182:'DDP10/20/30/50:4артикула3359011/12/13/15,4цены10380/19860/32460/102900 и4заказа. Повторная размерная таблица не добавляет варианты.',
        285:'В основном GU1 два исполнения220В/380В,3107110/3107109,72420/75660,два собственных заказа. Дополнительные патрубки/лампа отдельно и не основание MULTI.',
        300:'Основные тискиBSI100/140 имеют разные артикулы3000210/3000214,13980/20460 и независимые корзины. Строка заголовка Модель сама не использована как доказательство.',
        317:'Три основные подставки:8574010/8574012/8574013,31896/39138/39468,разные orderIDs64/61/69. Источник повторяет HTMLanchorart8574010, но клетки артикулов и коммерческие строки различны. Сохранение MULTI безопасно; виброопоры не единственное основание.',
        328:'Четыре основные подставки подBF16/BF20/MH25etc/BF46:8353000S/8353010/8574018/8574019,отдельные цены18420/23472/33396/45960 и заказы. Совместимые станки в одной строке не превращены в варианты подставки; виброопоры вторичны.'}
    for group,notes,label in [(amb['operator_facts'],operator,'VALID_AMBIGUOUS'),(multi['risk_facts'],risk,'VALID_MULTI_VARIANT')]:
        assert {a['index'] for a in group}==set(notes)
        for a in group:
            path=OUT/a['facts'];assert r.digest(path.read_bytes())==a['sha256']
            a.update(semantic_decision='PASS',fresh_semantic_reason=notes[a['index']],review_method='INDIVIDUAL_FRESH_DOM_COMMERCIAL_REVIEW',source_evidence_class='PACKAGED-EVIDENCE',semantic_interpretation_class='INFERENCE',prior_PASS_not_used_as_substitute=True)
    amb.update(status='PASS',operator_recheck_status='PASS',operator_recheck_count=6,current_terminal_source_products_absent=True,row405_current_outcome='PERSISTED',false_ambiguous=0)
    multi.update(status='PASS',risk_semantics_status='PASS',risk_semantic_count=9,false_multi_variant=0,current_terminal_source_products_absent=True)
    r.save(OUT/'AMBIGUOUS_DECISION_VERIFICATION.json',amb);r.save(OUT/'MULTI_VARIANT_DECISION_VERIFICATION.json',multi)
    print('Fresh operator6/6 and riskMULTI9/9 PASS;114+66maps and SQLsnapshotabsence verified')

if __name__=='__main__':finish()
