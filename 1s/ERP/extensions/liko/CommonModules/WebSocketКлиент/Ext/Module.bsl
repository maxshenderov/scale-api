
Процедура ОбработкаСообщения(Форма,Элемент, ДанныеСобытия, СтандартнаяОбработка) Экспорт 
	Если НЕ ЗначениеЗаполнено(ДанныеСобытия.Button.id) Тогда
		Возврат;
	КонецЕсли;
	Если ДанныеСобытия.Button.id="on-subscribed" Тогда
		 //TalkUuid=JSONВСтруктуру(Элементы.ПолеHTML.Документ.forms["form"].talk.Value).uuid;
		 Форма.WebSocketUUID=JSONВСтруктуру(Форма.Элементы.WebSocketURL.Документ.forms["form"].talk.Value).uuid;
		 Если Форма.WebSocketПоказыватьQRCode Тогда
			  Элемент.Ширина                 = 19;
	          Элемент.Высота                 = 20; 			 
		 КонецЕсли; 	 
	КонецЕсли;
	
		
	Док = Форма.Элементы.WebSocketURL.Документ;
	JSON=Док.forms["form"].message.Value;
	//Сообщить(JSON);
	
	Если ЗначениеЗаполнено(JSON) Тогда		
		Форма.WebSocketMessage=JSON;
	
		ЧтениеПотокаJSON = Новый ЧтениеJSON;
		ЧтениеПотокаJSON.УстановитьСтроку(JSON);
		Результат=ПрочитатьJSON(ЧтениеПотокаJSON);
		Форма.WebSocketMessage=Результат;
	    Контент=Результат.content;
		Если Контент.FormGUID=Форма.FormGUID Тогда 
			Возврат;
		КонецЕсли;
		
		Если  Контент.Свойство("FormUpdate") Тогда
			 ЗаполнитьЗначенияСвойств(Форма,Контент.FormUpdate);
		КонецЕсли;
		Если  Контент.Свойство("ScanData") Тогда
			Попытка
			 Форма.WebSocketОбработкаШтрихКода();	
			Исключение
			    //ОписаниеОшибки()
			КонецПопытки;
						
		КонецЕсли;

		
		
		
	КонецЕсли;

	
КонецПроцедуры

Процедура СоздатьОбсуждениеСтарый(Форма) Экспорт //+Лико m.shenderov 19.06.2021
Settings=WebSocketСервер.Settings();
    Док = Форма.Элементы.WebSocketURL.Документ; 
	Док.forms["form"].subject.Value  = Форма.WebSocketSubject; 
	Док.forms["form"].username.Value = Settings.username;
	Док.forms["form"].password.Value = Settings.password;

							


		
КонецПроцедуры //-Лико m.shenderov 19.06.2021


Процедура СоздатьОбсуждение(Форма,ДанныеСобытия) Экспорт //+Лико m.shenderov 19.06.2021
	
	Если НЕ ЗначениеЗаполнено(ДанныеСобытия.Button.id) Или 
		ДанныеСобытия.Button.id<>"on-loaded" Тогда
		Возврат;
	КонецЕсли;
	
	
	Settings=WebSocketСервер.Settings();
    Док = Форма.Элементы.WebSocketURL.Документ; 
	//Сообщить(Док);
	//Попытка
	
	//Док.forms["form"].debug.Value  = 1; 
	//Сообщить("1"+Док.forms["form"].subject.Value);

	Док.forms["form"].subject.Value  = Форма.WebSocketSubject; 
	Док.forms["form"].username.Value = Settings.username;
	Док.forms["form"].password.Value = Settings.password;
	
	//Сообщить("2"+Док.forms["form"].subject.Value);

		
	//Исключение
	    //ОписаниеОшибки()
	//КонецПопытки;
	
							


		
КонецПроцедуры //-Лико m.shenderov 19.06.2021


Функция JSONВСтруктуру(JSON) //+Лико m.shenderov 17.06.2021
		Если ЗначениеЗаполнено(JSON) Тогда
		
		ЧтениеПотокаJSON = Новый ЧтениеJSON;
		ЧтениеПотокаJSON.УстановитьСтроку(JSON);
		Возврат ПрочитатьJSON(ЧтениеПотокаJSON);
	КонецЕсли;
	
КонецФункции //-Лико m.shenderov 17.06.2021 

