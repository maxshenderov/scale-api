#Если Сервер Или ТолстыйКлиентОбычноеПриложение Или ВнешнееСоединение Тогда

#Область СлужебныйПрограммныйИнтерфейс

Функция НормативныйДокумент(Значение) Экспорт
	
	Если Значение = ФЗ79 Тогда
		
		Возврат НСтр("ru = 'Федеральный закон от 27.07.2004 № 79-ФЗ ""О государственной гражданской службе Российской Федерации""';
					|en = 'Federal Law dated 27.07.2004 No. 79-FZ ""On the State Civil Service of the Russian Federation""'");
		
	ИначеЕсли Значение = ЗРФ31321 Тогда
		
		Возврат НСтр("ru = 'Закон РФ от 26.06.1992 № 3132-1 ""О статусе судей в Российской Федерации""';
					|en = 'Law of the Russian Federation dated 26.06.1992 No. 3132-1 ""On the Status of Judges in the Russian Federation""'");
		
	ИначеЕсли Значение = ФЗ25 Тогда
		
		Возврат НСтр("ru = 'Федеральный закон от 02.03.2007 № 25-ФЗ ""О муниципальной службе в Российской Федерации""';
					|en = 'Federal Law dated 02.03.2007 No. 25-FZ ""On Municipal Service in the Russian Federation""'");
		
	ИначеЕсли Значение = ФЗ41 Тогда
		
		Возврат НСтр("ru = 'Федеральный закон от 08.05.1996 № 41-ФЗ ""О производственных кооперативах""';
					|en = 'Federal Law dated 08.05.1996 No. 41-FZ ""On Production Cooperatives""'");
		
	ИначеЕсли Значение = ФЗ328 Тогда
		
		Возврат НСтр("ru = 'Федеральный закон от 01.10.2019 № 328-ФЗ ""О службе в органах принудительного исполнения Российской Федерации и внесении изменений в отдельные законодательные акты Российской Федерации""';
					|en = 'Federal Law dated 01.10.2019 No. 328-FZ ""On Service in the Compulsory Enforcement Bodies of the Russian Federation and Amending Certain Legislative Acts of the Russian Federation""'");
		
	ИначеЕсли Значение = ФЗ342 Тогда
		
		Возврат НСтр("ru = 'Федеральный закон от 30.11.2011 № 342-ФЗ ""О службе в органах внутренних дел Российской Федерации и внесении изменений в отдельные законодательные акты Российской Федерации""';
					|en = 'Federal Law dated 30.11.2011 No. 342-FZ ""On Service in the Internal Affairs Bodies of the Russian Federation and Amending Certain Legislative Acts of the Russian Federation""'");
		
	ИначеЕсли Значение = ФЗ113 Тогда
		
		Возврат НСтр("ru = 'Федеральный закон от 25.07.2002 № 113-ФЗ ""Об альтернативной гражданской службе""';
					|en = 'Federal Law of 25.07.2002 No. 113-FZ ""On Alternative Civil Service""'");
		
	ИначеЕсли Значение = УИКРФ Тогда
		
		Возврат НСтр("ru = 'Уголовно-исполнительный кодекс Российской Федерации';
					|en = 'Criminal penal code of the Russian Federation'");
		
	ИначеЕсли Значение = ФЗ114 Тогда
		
		Возврат НСтр("ru = 'Федеральный закон от 21.07.1997 № 114-ФЗ ""О службе в таможенных органах Российской Федерации""';
					|en = 'Federal Law dated July 21, 1997 No. 114--FZ ""On Service in the Customs Authorities of the Russian Federation""'");
		
	ИначеЕсли Значение = ФЗ141 Тогда
		
		Возврат НСтр("ru = 'Федеральный закон от 23.05.2016 № 141-ФЗ ""О службе в федеральной противопожарной службе Государственной противопожарной службы и внесении изменений в отдельные законодательные акты Российской Федерации""';
					|en = 'Federal Law dated May 23, 2016 No. 141-FZ ""On Service in the Federal Fire Service of the State Fire Service and Amendments to Certain Legislative Acts of the Russian Federation""'");
		
	ИначеЕсли Значение = ФЗ197 Тогда
		
		Возврат НСтр("ru = 'Федеральный закон от 19.07.2018 № 197-ФЗ ""О службе в уголовно-исполнительной системе Российской Федерации и о внесении изменений в Закон Российской Федерации ""Об учреждениях и органах, исполняющих уголовные наказания в виде лишения свободы""';
					|en = 'Federal Law dated July 19, 2018 No. 197-FZ ""On Service in the Penitentiary System of the Russian Federation and on Amendments to the Law of the Russian Federation"" On Institutions and Bodies Executing Criminal Sentences in the Form of Imprisonment""'");
		
	ИначеЕсли Значение = ФЗ403 Тогда
		
		Возврат НСтр("ru = 'Федеральный закон от 28.12.2010 № 403-ФЗ ""О Следственном комитете Российской Федерации""';
					|en = 'Federal Law dated December 28, 2010 No. 403-FZ ""On the Investigative Committee of the Russian Federation""'");
		
	ИначеЕсли Значение = ФЗ53 Тогда
		
		Возврат НСтр("ru = 'Федеральный закон от 28.03.1998 № 53-ФЗ ""О воинской обязанности и военной службе""';
					|en = 'Federal Law dated March 28, 1998 No. 53-FZ ""On military duty and military service""'");

	КонецЕсли;
	
	Возврат НСтр("ru = 'Трудовой кодекс Российской Федерации';
				|en = 'Labor Code of the Russian Federation'");
	
КонецФункции

Функция НормативныйДокументВРодительномПадеже(Значение) Экспорт
	
	Если Значение = ФЗ79 Тогда
		
		Возврат НСтр("ru = 'Федерального закона от 27.07.2004 № 79-ФЗ ""О государственной гражданской службе Российской Федерации""';
					|en = 'of the Federal Law dated 27.07.2004 No. 79-FZ ""On the State Civil Service of the Russian Federation""'");
		
	ИначеЕсли Значение = ЗРФ31321 Тогда
		
		Возврат НСтр("ru = 'Закона РФ от 26.06.1992 № 3132-1 ""О статусе судей в Российской Федерации""';
					|en = 'of the Law of the Russian Federation dated 26.06.1992 No. 3132-1 ""On the Status of Judges in the Russian Federation""'");
		
	ИначеЕсли Значение = ФЗ25 Тогда
		
		Возврат НСтр("ru = 'Федерального закона от 02.03.2007 № 25-ФЗ ""О муниципальной службе в Российской Федерации""';
					|en = 'of the Federal Law dated 02.03.2007 No. 25-FZ ""On Municipal Service in the Russian Federation""'");
		
	ИначеЕсли Значение = ФЗ41 Тогда
		
		Возврат НСтр("ru = 'Федерального закона от 08.05.1996 № 41-ФЗ ""О производственных кооперативах""';
					|en = 'of the Federal Law dated 08.05.1996 No. 41-FZ ""On Production Cooperatives""'");
		
	ИначеЕсли Значение = ФЗ328 Тогда
		
		Возврат НСтр("ru = 'Федерального закона от 01.10.2019 № 328-ФЗ ""О службе в органах принудительного исполнения Российской Федерации и внесении изменений в отдельные законодательные акты Российской Федерации""';
					|en = 'of the Federal Law dated 01.10.2019 No. 328-FZ ""On Service in the Compulsory Enforcement Bodies of the Russian Federation and Amending Certain Legislative Acts of the Russian Federation""'");
		
	ИначеЕсли Значение = ФЗ342 Тогда
		
		Возврат НСтр("ru = 'Федерального закона от 30.11.2011 № 342-ФЗ ""О службе в органах внутренних дел Российской Федерации и внесении изменений в отдельные законодательные акты Российской Федерации""';
					|en = 'of the Federal Law dated 30.11.2011 No. 342-FZ ""On Service in the Internal Affairs Bodies of the Russian Federation and Amending Certain Legislative Acts of the Russian Federation""'");
		
	ИначеЕсли Значение = ФЗ113 Тогда
		
		Возврат НСтр("ru = 'Федерального закона от 25.07.2002 № 113-ФЗ ""Об альтернативной гражданской службе""';
					|en = 'of the Federal Law of 25.07.2002 No. 113-FZ ""On Alternative Civil Service""'");
		
	ИначеЕсли Значение = УИКРФ Тогда
		
		Возврат НСтр("ru = 'Уголовно-исполнительного кодекса Российской Федерации';
					|en = 'of the Criminal penal code of the Russian Federation'");
		
	ИначеЕсли Значение = ФЗ114 Тогда
		
		Возврат НСтр("ru = 'Федерального закона от 21.07.1997 № 114-ФЗ ""О службе в таможенных органах Российской Федерации""';
					|en = 'Federal Law dated July 21, 1997 No. 114--FZ ""On Service in the Customs Authorities of the Russian Federation""'");
		
	ИначеЕсли Значение = ФЗ141 Тогда
		
		Возврат НСтр("ru = 'Федерального закона от 23.05.2016 № 141-ФЗ ""О службе в федеральной противопожарной службе Государственной противопожарной службы и внесении изменений в отдельные законодательные акты Российской Федерации""';
					|en = 'Federal Law dated May 23, 2016 No. 141-FZ ""On Service in the Federal Fire Service of the State Fire Service and Amendments to Certain Legislative Acts of the Russian Federation""'");
		
	ИначеЕсли Значение = ФЗ197 Тогда
		
		Возврат НСтр("ru = 'Федерального закона от 19.07.2018 № 197-ФЗ ""О службе в уголовно-исполнительной системе Российской Федерации и о внесении изменений в Закон Российской Федерации ""Об учреждениях и органах, исполняющих уголовные наказания в виде лишения свободы""';
					|en = 'Federal Law dated July 19, 2018 No. 197-FZ ""On Service in the Penitentiary System of the Russian Federation and on Amendments to the Law of the Russian Federation"" On Institutions and Bodies Executing Criminal Sentences in the Form of Imprisonment""'");
		
	ИначеЕсли Значение = ФЗ403 Тогда
		
		Возврат НСтр("ru = 'Федерального закона от 28.12.2010 № 403-ФЗ ""О Следственном комитете Российской Федерации""';
					|en = 'Federal Law dated December 28, 2010 No. 403-FZ ""On the Investigative Committee of the Russian Federation""'");
		
	ИначеЕсли Значение = ФЗ53 Тогда
		
		Возврат НСтр("ru = 'Федерального закона от 28.03.1998 № 53-ФЗ ""О воинской обязанности и военной службе""';
					|en = 'Federal Law dated March 28, 1998 No. 53-FZ ""On military duty and military service""'");

	КонецЕсли;
	
	Возврат НСтр("ru = 'Трудового кодекса Российской Федерации';
				|en = 'of the Labor Code of the Russian Federation'");
	
КонецФункции

#КонецОбласти

#КонецЕсли