//Plugin to add text to an input field that disappears on focus
jQuery.fn.inputFieldText = function() {
    this.each(function() {
        var string = $(this).attr('title')
        $(this).val(string);
        $(this).focus(function(){
            if ($(this).val() == string){
                $(this).val('');
            }
        });
        $(this).blur(function(){
            if ($(this).val() == '' ){
                $(this).val(string);
            }
        });
    });
}
